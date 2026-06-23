#!/usr/bin/env python3
"""
印章真伪鉴定脚本
用法: python seal_verify.py <检材文件> <样本文件> [--output report.html]
"""

# ============================================================
# 第一阶段：venv 自管理
# ============================================================
import sys, os, subprocess
from pathlib import Path

VENV_DIR = Path(__file__).parent.parent / ".seal_venv"
REQUIREMENTS = [
    "opencv-python", "PyMuPDF", "scikit-image",
    "Pillow", "anthropic", "numpy",
]

def in_venv():
    return sys.prefix != sys.base_prefix

def setup_and_relaunch():
    if not VENV_DIR.exists():
        print("🔧 首次运行：正在创建虚拟环境...")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])
    pip    = VENV_DIR / ("Scripts" if sys.platform == "win32" else "bin") / "pip"
    python = VENV_DIR / ("Scripts" if sys.platform == "win32" else "bin") / "python"
    try:
        ok = subprocess.run(
            [str(python), "-c", "import cv2,fitz,skimage,PIL,anthropic,numpy"],
            capture_output=True).returncode == 0
    except Exception:
        ok = False
    if not ok:
        print("📦 正在安装依赖（仅首次约1-2分钟）...")
        subprocess.check_call([str(pip), "install", "--quiet", "--upgrade", "pip"])
        subprocess.check_call([str(pip), "install", "--quiet", *REQUIREMENTS])
        print("✅ 依赖安装完成")
    os.execv(str(python), [str(python)] + sys.argv)

if not in_venv():
    setup_and_relaunch()
    sys.exit(0)

# ============================================================
# 第二阶段：业务逻辑
# ============================================================
import argparse, base64, io, json, tempfile
from datetime import datetime

import cv2
import numpy as np
from PIL import Image, ImageFilter
from skimage.metrics import structural_similarity as ssim


# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────

def img_to_data_url(img: np.ndarray) -> str:
    _, buf = cv2.imencode(".png", img)
    return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode()

def pil_to_data_url(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

def cv2_to_pil(img: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))

def pil_to_cv2(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)


# ─────────────────────────────────────────────────────────────
# 文件加载
# ─────────────────────────────────────────────────────────────

def pdf_to_image(pdf_path: str) -> np.ndarray:
    import fitz
    doc = fitz.open(pdf_path)
    page = doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    doc.close()
    if pix.n == 4:   return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    if pix.n == 1:   return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    return arr

def load_image(file_path: str) -> np.ndarray:
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    if p.suffix.lower() == ".pdf":
        print(f"  📄 PDF转图像: {p.name}")
        return pdf_to_image(file_path)
    # cv2.imread 在 Windows 上不支持中文路径，改用 imdecode
    img_array = np.fromfile(file_path, dtype=np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"无法读取图像: {file_path}")
    return img


# ─────────────────────────────────────────────────────────────
# ★ 新增：文件真实性检测（PS/抠图痕迹）
# ─────────────────────────────────────────────────────────────

class ForgeryCheck:
    """单项检测结果"""
    def __init__(self, name, status, detail, image=None):
        # status: "ok" | "suspicious" | "skipped"
        self.name   = name
        self.status = status   # ok=绿 suspicious=红 skipped=灰
        self.detail = detail
        self.image  = image    # 可选：可视化图（PIL Image）


def check_ela(file_path: str, quality=75) -> ForgeryCheck:
    """
    ELA（错误级别分析）
    原理：以低质量重新JPEG压缩，粘贴区域因有不同压缩历史而误差更大
    适用：JPG / PDF内嵌JPG提取后
    不适用：PNG（无损格式，ELA无效）
    """
    suffix = Path(file_path).suffix.lower()
    is_pdf = suffix == ".pdf"
    is_jpg = suffix in (".jpg", ".jpeg")
    is_png = suffix == ".png"

    if is_png:
        return ForgeryCheck("ELA错误级别分析", "skipped",
                            "PNG为无损格式，ELA对其无效（无压缩历史差异可检测）")

    # 提取图像
    if is_pdf:
        import fitz
        doc = fitz.open(file_path)
        page = doc[0]
        # 尝试提取页面内嵌图像
        img_list = page.get_images(full=True)
        if not img_list:
            doc.close()
            return ForgeryCheck("ELA错误级别分析", "skipped",
                                "PDF中未找到嵌入图像对象，无法做ELA分析")
        # 取最大的嵌入图像
        best = max(img_list, key=lambda x: x[2] * x[3] if len(x) > 3 else 0)
        xref = best[0]
        base_img = doc.extract_image(xref)
        doc.close()
        if base_img["ext"] not in ("jpeg", "jpg"):
            return ForgeryCheck("ELA错误级别分析", "skipped",
                                f"PDF内嵌图像为{base_img['ext'].upper()}格式（非JPEG），ELA无效")
        orig = Image.open(io.BytesIO(base_img["image"])).convert("RGB")
    else:
        orig = Image.open(file_path).convert("RGB")

    # 重新以低质量保存再读回
    buf = io.BytesIO()
    orig.save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    recompressed = Image.open(buf).convert("RGB")

    # 计算误差，放大10倍可视化
    ela_arr = np.abs(np.array(orig, dtype=int) - np.array(recompressed, dtype=int))
    ela_arr = np.clip(ela_arr * 10, 0, 255).astype(np.uint8)
    ela_img = Image.fromarray(ela_arr)

    # 判断：印章区域（红色）的ELA值是否显著高于背景
    # 转HSV找红色区域
    orig_cv = pil_to_cv2(orig)
    hsv = cv2.cvtColor(orig_cv, cv2.COLOR_BGR2HSV)
    m1 = cv2.inRange(hsv, np.array([0,70,70]),   np.array([10,255,255]))
    m2 = cv2.inRange(hsv, np.array([160,70,70]), np.array([180,255,255]))
    red_mask = cv2.bitwise_or(m1, m2)

    ela_gray = np.array(ela_img.convert("L"))
    seal_ela  = ela_gray[red_mask > 0].mean() if red_mask.sum() > 0 else 0
    bg_ela    = ela_gray[red_mask == 0].mean()
    ratio     = seal_ela / (bg_ela + 1e-6)

    if red_mask.sum() == 0:
        status = "skipped"
        detail = "未在图像中检测到红色印章区域，无法定向ELA分析"
    elif ratio > 2.5:
        status = "suspicious"
        detail = (f"印章区域ELA误差均值({seal_ela:.1f})显著高于背景({bg_ela:.1f})，"
                  f"比值{ratio:.1f}x（阈值2.5x）→ 疑似后期粘贴")
    else:
        status = "ok"
        detail = (f"印章区域ELA误差({seal_ela:.1f})与背景({bg_ela:.1f})比值{ratio:.1f}x，"
                  f"在正常范围内")

    return ForgeryCheck("ELA错误级别分析", status, detail, ela_img)


def check_pdf_metadata(file_path: str) -> ForgeryCheck:
    """检查PDF元数据：创建工具、修改历史、可疑软件痕迹"""
    if Path(file_path).suffix.lower() != ".pdf":
        return ForgeryCheck("PDF元数据分析", "skipped",
                            "非PDF文件，跳过元数据检查")
    import fitz
    doc = fitz.open(file_path)
    meta = doc.metadata
    doc.close()

    suspicious_tools = ["photoshop", "gimp", "illustrator", "inkscape",
                        "acrobat", "nitro", "foxit", "pdfedit"]
    findings = []
    creator  = (meta.get("creator",  "") or "").lower()
    producer = (meta.get("producer", "") or "").lower()

    for tool in suspicious_tools:
        if tool in creator or tool in producer:
            findings.append(tool.title())

    created  = meta.get("creationDate", "未知")
    modified = meta.get("modDate",      "未知")
    creator_raw  = meta.get("creator",  "未知")
    producer_raw = meta.get("producer", "未知")

    detail = (f"创建工具: {creator_raw} | 生成工具: {producer_raw} | "
              f"创建时间: {created} | 修改时间: {modified}")

    if findings:
        return ForgeryCheck("PDF元数据分析", "suspicious",
                            f"检测到图像编辑软件痕迹：{', '.join(findings)}。{detail}")
    return ForgeryCheck("PDF元数据分析", "ok", detail)


def check_pdf_layers(file_path: str) -> ForgeryCheck:
    """
    检查PDF图层结构：
    真实盖章 → 印章油墨与文字在同一内容流中
    PS合成   → 印章通常是独立的图像对象（XObject），浮于文字之上
    """
    if Path(file_path).suffix.lower() != ".pdf":
        return ForgeryCheck("PDF图层结构分析", "skipped", "非PDF文件")

    import fitz
    doc  = fitz.open(file_path)
    page = doc[0]

    # 获取页面中所有图像对象
    img_list = page.get_images(full=True)
    # 获取页面文字块
    text_blocks = page.get_text("blocks")
    doc.close()

    n_imgs = len(img_list)
    n_text = len(text_blocks)

    if n_imgs == 0:
        return ForgeryCheck("PDF图层结构分析", "ok",
                            f"页面无独立图像对象，文字块{n_text}个 → 印章为原生内容流（正常）")

    # 如果图像数量异常多，可疑
    if n_imgs >= 3:
        return ForgeryCheck("PDF图层结构分析", "suspicious",
                            f"页面含{n_imgs}个独立图像对象（阈值3）→ 疑似合成文件，"
                            f"印章可能为后期插入的图像层")

    return ForgeryCheck("PDF图层结构分析", "ok",
                        f"页面含{n_imgs}个图像对象，{n_text}个文字块，结构正常")


def check_noise_consistency(img_cv: np.ndarray, file_path: str) -> ForgeryCheck:
    """
    噪点一致性检测：
    真实扫描件 → 全图噪点分布均匀
    PS粘贴章   → 印章区域噪点模式与背景不同（来自不同来源）
    """
    suffix = Path(file_path).suffix.lower()
    # PNG通常是数字生成，噪点分析意义不大
    if suffix == ".png":
        # 仍然做，但降低判断敏感度
        threshold = 3.0
        note = "（PNG数字文件，阈值宽松）"
    else:
        threshold = 1.8
        note = ""

    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY).astype(float)

    # 高通滤波提取噪点层
    blur   = cv2.GaussianBlur(gray, (5,5), 0)
    noise  = np.abs(gray - blur)

    # 找红色印章区域
    hsv = cv2.cvtColor(img_cv, cv2.COLOR_BGR2HSV)
    m1 = cv2.inRange(hsv, np.array([0,70,70]),   np.array([10,255,255]))
    m2 = cv2.inRange(hsv, np.array([160,70,70]), np.array([180,255,255]))
    red_mask = cv2.bitwise_or(m1, m2)

    if red_mask.sum() < 500:
        return ForgeryCheck("噪点一致性分析", "skipped",
                            "未检测到足够的红色印章区域，无法对比噪点分布")

    seal_noise = noise[red_mask > 0].mean()
    bg_noise   = noise[red_mask == 0].mean()
    ratio      = seal_noise / (bg_noise + 1e-6)

    if ratio > threshold:
        status = "suspicious"
        detail = (f"印章区域噪点({seal_noise:.2f})显著高于背景({bg_noise:.2f})，"
                  f"比值{ratio:.1f}x（阈值{threshold}x）{note} → 疑似来源不一致")
    elif ratio < 1/threshold:
        status = "suspicious"
        detail = (f"印章区域噪点({seal_noise:.2f})显著低于背景({bg_noise:.2f})，"
                  f"比值{ratio:.1f}x → 印章疑似数字合成后贴入扫描文件")
    else:
        status = "ok"
        detail = (f"印章区域噪点({seal_noise:.2f})与背景({bg_noise:.2f})"
                  f"比值{ratio:.1f}x，分布一致{note}")

    return ForgeryCheck("噪点一致性分析", status, detail)


def check_edge_sharpness(img_cv: np.ndarray) -> ForgeryCheck:
    """
    边缘锐利度检测：
    真实盖章 → 油墨自然扩散，边缘有随机模糊（Laplacian方差较低）
    数字打印/PS → 边缘过于锐利（Laplacian方差高）
    """
    hsv = cv2.cvtColor(img_cv, cv2.COLOR_BGR2HSV)
    m1 = cv2.inRange(hsv, np.array([0,70,70]),   np.array([10,255,255]))
    m2 = cv2.inRange(hsv, np.array([160,70,70]), np.array([180,255,255]))
    red_mask = cv2.bitwise_or(m1, m2)

    if red_mask.sum() < 500:
        return ForgeryCheck("印章边缘锐利度", "skipped",
                            "未检测到足够的红色印章区域")

    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)

    # 只在印章边缘区域（膨胀-腐蚀=边缘带）计算
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9,9))
    dilated = cv2.dilate(red_mask, kernel)
    eroded  = cv2.erode(red_mask, kernel)
    edge_band = cv2.bitwise_xor(dilated, eroded)

    lap = cv2.Laplacian(gray, cv2.CV_64F)
    edge_sharpness = np.abs(lap)[edge_band > 0].mean()

    # 经验阈值：真实盖章<8，数字打印>15
    if edge_sharpness > 15:
        status = "suspicious"
        detail = (f"印章边缘锐利度{edge_sharpness:.1f}（阈值15）→ 边缘过于锐利，"
                  f"疑似数字打印或PS合成，缺乏真实盖章的油墨扩散特征")
    elif edge_sharpness < 2:
        status = "ok"
        detail = f"印章边缘锐利度{edge_sharpness:.1f}，边缘自然模糊，符合真实盖章特征"
    else:
        status = "ok"
        detail = f"印章边缘锐利度{edge_sharpness:.1f}，在正常范围内"

    return ForgeryCheck("印章边缘锐利度", status, detail)


def run_forgery_checks(file_path: str, img_cv: np.ndarray) -> list[ForgeryCheck]:
    """自动运行所有适用的检测，不适用的标注原因"""
    print("  🔎 运行文件真实性检测...")
    checks = []

    # 1. ELA
    c = check_ela(file_path)
    checks.append(c)
    print(f"    ELA: {c.status} — {c.detail[:60]}...")

    # 2. PDF元数据（仅PDF）
    c = check_pdf_metadata(file_path)
    checks.append(c)
    print(f"    元数据: {c.status} — {c.detail[:60]}...")

    # 3. PDF图层（仅PDF）
    c = check_pdf_layers(file_path)
    checks.append(c)
    print(f"    图层: {c.status} — {c.detail[:60]}...")

    # 4. 噪点一致性
    c = check_noise_consistency(img_cv, file_path)
    checks.append(c)
    print(f"    噪点: {c.status} — {c.detail[:60]}...")

    # 5. 边缘锐利度
    c = check_edge_sharpness(img_cv)
    checks.append(c)
    print(f"    边缘: {c.status} — {c.detail[:60]}...")

    return checks


# ─────────────────────────────────────────────────────────────
# 印章检测与比对（原有逻辑）
# ─────────────────────────────────────────────────────────────

def detect_seal(img: np.ndarray) -> tuple:
    """
    检测图像中的红色印章区域，返回裁剪后的印章图像和位置信息。

    策略：
    1. 在原始红色掩膜上做轻量闭运算（仅填充微小孔洞，不抹除薄片断）
    2. 直接 findContours → 过滤噪声碎片（面积 < 阈值）
    3. 对所有有效轮廓取并集边界框（union bounding box），而非单个最大轮廓
    4. 即使印章被文字/线条割裂成多块，也能完整截取整个印章
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m1  = cv2.inRange(hsv, np.array([0,70,70]),   np.array([10,255,255]))
    m2  = cv2.inRange(hsv, np.array([160,70,70]), np.array([180,255,255]))
    red_mask = cv2.bitwise_or(m1, m2)

    if red_mask.sum() == 0:
        print("  ⚠️  未检测到红色印章，使用整图")
        return img, (0, 0, img.shape[1], img.shape[0]), False

    # 轻量闭运算：仅填充微小孔洞（3×3），不抹除印章薄片断
    tiny_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_CLOSE, tiny_kernel)

    # 在原始掩膜上找轮廓（不过度预处理，保留所有印章碎片）
    contours, _ = cv2.findContours(red_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        print("  ⚠️  未检测到红色印章轮廓，使用整图")
        return img, (0, 0, img.shape[1], img.shape[0]), False

    # 过滤噪声碎片：保留面积 >= 图像总面积 0.02% 的轮廓
    img_area = img.shape[0] * img.shape[1]
    min_contour_area = max(300, img_area * 0.0002)  # 至少 300px，或图像面积的 0.02%
    seal_contours = [c for c in contours if cv2.contourArea(c) >= min_contour_area]

    if not seal_contours:
        print(f"  ⚠️  所有红色轮廓面积均 < {min_contour_area:.0f}px（噪声），使用整图")
        return img, (0, 0, img.shape[1], img.shape[0]), False

    total_area = sum(cv2.contourArea(c) for c in seal_contours)
    if total_area < 1000:
        print(f"  ⚠️  有效印章区域过小({total_area:.0f}px)，使用整图")
        return img, (0, 0, img.shape[1], img.shape[0]), False

    # ★ 取所有有效轮廓的并集边界框（union bounding box）
    all_points = np.vstack([c.reshape(-1, 2) for c in seal_contours])
    x = int(np.min(all_points[:, 0]))
    y = int(np.min(all_points[:, 1]))
    w = int(np.max(all_points[:, 0]) - x + 1)
    h = int(np.max(all_points[:, 1]) - y + 1)

    pad = int(max(w, h) * 0.1)
    x = max(0, x - pad)
    y = max(0, y - pad)
    w = min(img.shape[1] - x, w + 2 * pad)
    h = min(img.shape[0] - y, h + 2 * pad)

    print(f"  ✅ 检测到印章 ({x},{y}) {w}×{h}px，{len(seal_contours)}个有效碎片，总面积{total_area:.0f}px²")
    return img[y:y+h, x:x+w], (x, y, w, h), True


# ─────────────────────────────────────────────────────────────
# ★ 五角星星心检测与配准（以星心为基准对齐两枚印章）
# ─────────────────────────────────────────────────────────────

def detect_five_pointed_star(seal_img: np.ndarray) -> dict:
    """
    在一枚印章图像中检测中央五角星，返回星心坐标、外接圆半径、五个顶点。

    检测策略（凸包五边形法，比 convexityDefects 更鲁棒）：
    1. HSV 提取红色掩膜 → 取图像中央区域（星心必在中心）
    2. findContours → 对每个轮廓计算凸包
    3. 将凸包近似为多边形：若恰好 5 个顶点且近似正五边形 → 即五角星的 5 个尖端
    4. 验证：5 顶点到中心的距离标准差 < 15%（正五边形特征）

    Returns:
        {'success': bool, 'center': (cx,cy), 'radius': float, 'tips': np.ndarray(5,2)}
        失败时 success=False 并附带 'reason' 字段。
    """
    h, w = seal_img.shape[:2]
    img_area = h * w

    # ── 提取红色区域 ──
    hsv = cv2.cvtColor(seal_img, cv2.COLOR_BGR2HSV)
    m1 = cv2.inRange(hsv, np.array([0, 70, 70]), np.array([10, 255, 255]))
    m2 = cv2.inRange(hsv, np.array([160, 70, 70]), np.array([180, 255, 255]))
    red_mask = cv2.bitwise_or(m1, m2)

    if red_mask.sum() < 100:
        return {'success': False, 'reason': '无红色区域'}

    # ── 中央区域（星心不会在边缘）──
    cx_img, cy_img = w // 2, h // 2
    center_radius = int(min(w, h) * 0.40)  # 40% 略大于上一版，低分辨率下更宽容
    center_circle = np.zeros(red_mask.shape, dtype=np.uint8)
    cv2.circle(center_circle, (cx_img, cy_img), center_radius, 255, -1)
    red_center = cv2.bitwise_and(red_mask, center_circle)

    if red_center.sum() < 80:
        return {'success': False, 'reason': '中央红色区域过小（可能无星）'}

    # ── 多策略尝试：raw / close / dilate ──
    candidates = []  # (contour, hull_vertices_5, center, radius, score)

    for label, mask in [
        ("raw",   red_center),
        ("close", cv2.morphologyEx(red_center, cv2.MORPH_CLOSE,
                    cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))),
        ("dilate", cv2.dilate(red_center,
                    cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)), iterations=1)),
    ]:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area = cv2.contourArea(c)
            if area < 20:
                continue

            # 凸包 → 多边形近似
            hull = cv2.convexHull(c)
            peri = cv2.arcLength(hull, True)
            # 自适应 epsilon：轮廓周长的 2%~4%
            for eps_factor in [0.02, 0.03, 0.04]:
                hull_approx = cv2.approxPolyDP(hull, eps_factor * peri, True)
                n_verts = len(hull_approx)
                if n_verts == 5:
                    verts = hull_approx.reshape(-1, 2).astype(np.float32)
                    v_center = verts.mean(axis=0)
                    v_dists  = np.linalg.norm(verts - v_center, axis=1)
                    v_mean   = v_dists.mean()
                    if v_mean < 1:
                        continue
                    v_cv = v_dists.std() / v_mean  # 变异系数，越小越接近正多边形

                    # 靠近图像中心
                    dist_to_img_center = np.sqrt((v_center[0]-cx_img)**2 + (v_center[1]-cy_img)**2)
                    cent_score = max(0.0, 1.0 - dist_to_img_center / (center_radius * 0.6))

                    # 正五边形特征：变异系数 < 15%
                    if v_cv < 0.18:
                        score = area * cent_score * (1.0 - v_cv)
                        candidates.append((c, verts, v_center, v_mean, score))
                    break  # 找到 5 顶点就不再试更大 epsilon

    if not candidates:
        return {'success': False, 'reason': f'中央区域无正五边形凸包（共{len(contours) if "contours" in dir() else 0}个轮廓）'}

    # ── 选最优：面积大 + 靠近中心 + 正五边形变异系数小 ──
    best = max(candidates, key=lambda x: x[4])
    _, verts, center, radius, _ = best

    # ── 用凸包径向距离剖面找峰（比 approxPolyDP 顶点更稳定）──
    # approxPolyDP 对轮廓噪声敏感，不同图像可能选出不同的顶点集合。
    # 改为：取最佳候选的 hull 点，计算径向距离剖面，找 5 个峰。
    c, hull = best[0], cv2.convexHull(best[0])
    hull_pts = hull.reshape(-1, 2).astype(np.float32)
    cx_h = float(center[0])
    cy_h = float(center[1])

    # 计算各 hull 点的角度和径向距离
    h_angles = np.arctan2(hull_pts[:, 0] - cx_h, -(hull_pts[:, 1] - cy_h))
    h_dists  = np.linalg.norm(hull_pts - np.array([cx_h, cy_h]), axis=1)

    # 按角度排序
    sort_i = np.argsort(h_angles % (2 * np.pi))
    h_angles = h_angles[sort_i]
    h_dists  = h_dists[sort_i]

    # 找局部最大值（峰=星尖）：距离大于左右邻居
    n = len(h_dists)
    peaks = []
    for i in range(n):
        prev_i = (i - 1) % n
        next_i = (i + 1) % n
        if h_dists[i] > h_dists[prev_i] and h_dists[i] > h_dists[next_i]:
            peaks.append((h_angles[i], h_dists[i], hull_pts[sort_i][i]))

    if len(peaks) < 5:
        # 回退：用 approxPolyDP 顶点
        angles = np.arctan2(verts[:, 0] - cx_h, -(verts[:, 1] - cy_h))
        sorted_idx = np.argsort(angles % (2 * np.pi))
        tips = verts[sorted_idx]
    else:
        # 保留最高的 5 个峰
        peaks.sort(key=lambda x: x[1], reverse=True)
        peaks = peaks[:5]
        # 按角度排序
        peaks.sort(key=lambda x: x[0] % (2 * np.pi))
        tips = np.array([p[2] for p in peaks], dtype=np.float32)
        # 重新计算中心（以峰点质心）
        center = tuple(tips.mean(axis=0).astype(float))
        radius = float(np.mean(np.linalg.norm(tips - np.array(center), axis=1)))

    return {
        'success': True,
        'center': (float(center[0]), float(center[1])),
        'radius': float(radius),
        'tips': tips,
    }


def _fine_align(query: np.ndarray, reference: np.ndarray,
                max_shift=15, max_rot=3.0) -> np.ndarray:
    """
    ECC 亚像素微调：在星尖粗对齐后，用 findTransformECC 修正残留的
    平移（±max_shift px）和微旋转（±max_rot deg）。
    """
    gq = cv2.cvtColor(query, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gr = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY).astype(np.float32)

    # 初始单位矩阵（ECC 从此出发迭代优化）
    warp_matrix = np.eye(2, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-6)

    try:
        _, warp_matrix = cv2.findTransformECC(
            gr, gq, warp_matrix,
            cv2.MOTION_EUCLIDEAN,  # 允许平移+微旋转
            criteria,
            None, max_shift)       # max_pixel_step 限制搜索范围
        # 校验：旋转角不超过 max_rot 度
        ecc_rot = np.degrees(np.arctan2(warp_matrix[1, 0], warp_matrix[0, 0]))
        ecc_shift = max(abs(warp_matrix[0, 2]), abs(warp_matrix[1, 2]))
        if abs(ecc_rot) > max_rot or ecc_shift > 30:
            print(f'  ⚠️  ECC 异常: rot={ecc_rot:+.2f}deg shift={ecc_shift:.1f}px, 跳过微调')
            return query
        h, w = reference.shape[:2]
        aligned = cv2.warpAffine(query, warp_matrix, (w, h),
                                  flags=cv2.INTER_LANCZOS4 + cv2.WARP_INVERSE_MAP,
                                  borderMode=cv2.BORDER_CONSTANT,
                                  borderValue=(255, 255, 255))
        print(f'  🔧 ECC微调: rot={ecc_rot:+.2f}deg shift=({warp_matrix[0,2]:+.1f},{warp_matrix[1,2]:+.1f})px')
        return aligned
    except cv2.error as e:
        print(f'  ⚠️  ECC失败({e.err}), 跳过微调')
        return query


def align_by_star(seal_q: np.ndarray, seal_r: np.ndarray,
                  rot_q_steps: int = 0, rot_r_steps: int = 0) -> tuple:
    """
    Independent normalization: both seals rotated so star tip points UP.
    No rotation ambiguity - tips[0] always at 12 o'clock.
    rot_q_steps / rot_r_steps: extra 72° steps (0~4) for manual disambiguation.
    After coarse star alignment, ECC fine-tunes residual shift/rotation.
    Returns (aligned_q, aligned_r) at identical canvas size.
    """
    star_q = detect_five_pointed_star(seal_q)
    star_r = detect_five_pointed_star(seal_r)

    if not star_q['success'] or not star_r['success']:
        rq = star_q.get('reason', ''); rr = star_r.get('reason', '')
        print(f'  WARN star fail (q:{rq}|r:{rr}), fallback ORB')
        h_q, w_q = seal_q.shape[:2]; h_r, w_r = seal_r.shape[:2]
        ar = (h_r * w_r) / max(h_q * w_q, 1); s = np.sqrt(ar)
        nw, nh = max(1, int(w_q * s)), max(1, int(h_q * s))
        sq = cv2.resize(seal_q, (nw, nh), interpolation=cv2.INTER_LANCZOS4)
        cv = np.ones((h_r, w_r, 3), dtype=np.uint8) * 255
        yo, xo = max(0, (h_r - nh) // 2), max(0, (w_r - nw) // 2)
        ph, pw = min(nh, h_r), min(nw, w_r)
        cv[yo:yo+ph, xo:xo+pw] = sq[:ph, :pw]
        return align_seals(cv, seal_r), seal_r

    q_cx, q_cy = star_q['center']
    r_cx, r_cy = star_r['center']

    def _tip_deg(tip, cx, cy):
        return float(np.degrees(np.arctan2(tip[0] - cx, -(tip[1] - cy))))

    aq = _tip_deg(star_q['tips'][0], q_cx, q_cy)
    ar_deg = _tip_deg(star_r['tips'][0], r_cx, r_cy)

    # debug: show all 5 tip angles
    q_tips_deg = [_tip_deg(star_q['tips'][k], q_cx, q_cy) for k in range(5)]
    r_tips_deg = [_tip_deg(star_r['tips'][k], r_cx, r_cy) for k in range(5)]
    q_str = '[' + ', '.join(f'{t:+.1f}' for t in q_tips_deg) + ']'
    r_str = '[' + ', '.join(f'{t:+.1f}' for t in r_tips_deg) + ']'
    print(f'  STAR tips: Q={q_str}  R={r_str}')

    rot_q = -aq
    rot_r = -ar_deg

    scale = star_r['radius'] / max(star_q['radius'], 1e-6)
    csize = int(max(seal_r.shape[:2]) * 1.3)
    ccx = csize // 2
    ccy = csize // 2

    def _upright(img, cx, cy, deg, sc=1.0):
        rad = np.radians(deg)
        c = sc * np.cos(rad)
        s_val = sc * np.sin(rad)
        tx = ccx - (c * cx - s_val * cy)
        ty = ccy - (s_val * cx + c * cy)
        M = np.array([[c, -s_val, tx], [s_val, c, ty]], dtype=np.float64)
        return cv2.warpAffine(img, M, (csize, csize),
                               flags=cv2.INTER_LANCZOS4,
                               borderMode=cv2.BORDER_CONSTANT,
                               borderValue=(255, 255, 255))

    ar_img = _upright(seal_r, r_cx, r_cy, rot_r, 1.0)
    aq_img = _upright(seal_q, q_cx, q_cy, rot_q, scale)

    # ── 额外旋转（人工消歧，每次 72°，CSS 顺时针=OpenCV 逆时针需取负）──
    if rot_q_steps % 5 != 0:
        extra_q = rot_q_steps % 5 * 72.0
        M_q = cv2.getRotationMatrix2D((ccx, ccy), -extra_q, 1.0)  # CSS顺时针→CV负角
        aq_img = cv2.warpAffine(aq_img, M_q, (csize, csize),
                                 flags=cv2.INTER_LANCZOS4,
                                 borderMode=cv2.BORDER_CONSTANT,
                                 borderValue=(255, 255, 255))
        rot_q += extra_q   # log用正值（顺时针度数）
    if rot_r_steps % 5 != 0:
        extra_r = rot_r_steps % 5 * 72.0
        M_r = cv2.getRotationMatrix2D((ccx, ccy), -extra_r, 1.0)  # CSS顺时针→CV负角
        ar_img = cv2.warpAffine(ar_img, M_r, (csize, csize),
                                 flags=cv2.INTER_LANCZOS4,
                                 borderMode=cv2.BORDER_CONSTANT,
                                 borderValue=(255, 255, 255))
        rot_r += extra_r

    print(f'  STAR norm: q_rot={rot_q:+.1f}deg(+{rot_q_steps%5}*72) scale={scale:.3f}x '
          f'r_rot={rot_r:+.1f}deg(+{rot_r_steps%5}*72) canvas={csize}')

    # ── ECC 亚像素微调（修正星尖检测残留的偏移）──
    aq_img = _fine_align(aq_img, ar_img, max_shift=15, max_rot=3.0)

    return aq_img, ar_img


def align_seals(seal_q: np.ndarray, seal_r: np.ndarray) -> np.ndarray:
    gray_q = cv2.cvtColor(seal_q, cv2.COLOR_BGR2GRAY)
    gray_r = cv2.cvtColor(seal_r, cv2.COLOR_BGR2GRAY)
    orb    = cv2.ORB_create(1000)
    kp1, des1 = orb.detectAndCompute(gray_q, None)
    kp2, des2 = orb.detectAndCompute(gray_r, None)
    if des1 is None or des2 is None or len(kp1) < 4 or len(kp2) < 4:
        print("  ⚠️  特征点不足，跳过配准")
        return seal_q
    bf      = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = sorted(bf.match(des1, des2), key=lambda x: x.distance)[:50]
    if len(matches) < 4:
        print("  ⚠️  匹配点不足，跳过配准")
        return seal_q
    src_pts = np.float32([kp1[m.queryIdx].pt for m in matches]).reshape(-1,1,2)
    dst_pts = np.float32([kp2[m.trainIdx].pt for m in matches]).reshape(-1,1,2)
    M, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
    if M is None:
        return seal_q
    h, w = seal_r.shape[:2]
    aligned = cv2.warpPerspective(seal_q, M, (w, h),
                                  flags=cv2.INTER_LANCZOS4,
                                  borderMode=cv2.BORDER_CONSTANT,
                                  borderValue=(255,255,255))
    print(f"  ✅ 配准完成，使用{sum(mask.ravel())}个匹配点")
    return aligned


def analyze_difference(seal_q: np.ndarray, seal_r: np.ndarray) -> dict:
    gray_q = cv2.cvtColor(seal_q, cv2.COLOR_BGR2GRAY)
    gray_r = cv2.cvtColor(seal_r, cv2.COLOR_BGR2GRAY)
    score, diff = ssim(gray_r, gray_q, full=True)
    diff        = (diff * 255).astype(np.uint8)
    heatmap     = cv2.applyColorMap(255 - diff, cv2.COLORMAP_JET)

    overlay = np.zeros_like(seal_r)
    overlay[:,:,0] = gray_q
    overlay[:,:,2] = gray_r
    overlay[:,:,1] = np.minimum(gray_q, gray_r)

    _, diff_thresh  = cv2.threshold(255-diff, 50, 255, cv2.THRESH_BINARY)
    diff_contours,_ = cv2.findContours(diff_thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    overlay_marked  = overlay.copy()
    cv2.drawContours(overlay_marked, diff_contours, -1, (0,255,255), 2)

    diff_ratio = np.sum(diff_thresh > 0) / diff_thresh.size

    def red_mask(hsv):
        return cv2.bitwise_or(
            cv2.inRange(hsv, np.array([0,70,70]),   np.array([10,255,255])),
            cv2.inRange(hsv, np.array([160,70,70]), np.array([180,255,255])))

    rq = red_mask(cv2.cvtColor(seal_q, cv2.COLOR_BGR2HSV))
    rr = red_mask(cv2.cvtColor(seal_r, cv2.COLOR_BGR2HSV))
    ov = np.sum((rq>0)&(rr>0));  un = np.sum((rq>0)|(rr>0))
    red_iou = ov/un if un > 0 else 0

    # 检测尺寸比差异（两章面积悬殊时警告）
    area_q = np.sum(rq > 0);  area_r = np.sum(rr > 0)
    area_ratio = max(area_q, area_r) / (min(area_q, area_r) + 1)
    if area_ratio > 3:
        print(f"  ⚠️  两章印章区域面积比{area_ratio:.1f}x（阈值3x），检测结果可能不可靠，请人工确认")

    # ── RGBA 分层：叠加图拆成样本红层 + 检材蓝层，供 HTML 独立旋转 ──
    h, w = gray_r.shape

    # 样本层（底）：白底 + 红色墨迹 → 显示为红色
    # OpenCV BGRA: B=0, G=0, R=gray_r, A=255
    ref_layer = np.full((h, w, 4), 255, dtype=np.uint8)  # 白底不透明
    ink_r = gray_r < 245
    ref_layer[ink_r, 0] = 0              # B=0
    ref_layer[ink_r, 1] = 0              # G=0
    ref_layer[ink_r, 2] = gray_r[ink_r]  # R=样本灰度

    # 检材层（上）：透明底 + 蓝色墨迹 → 显示为蓝色
    # OpenCV BGRA: B=gray_q, G=0, R=0, A=半透明
    q_layer = np.zeros((h, w, 4), dtype=np.uint8)  # 透明
    ink_q = gray_q < 245
    q_layer[ink_q, 0] = gray_q[ink_q]   # B=检材灰度
    q_layer[ink_q, 1] = 0                # G=0
    q_layer[ink_q, 2] = 0                # R=0
    q_layer[ink_q, 3] = 200              # A=半透明(200/255≈78%)
    # 差异轮廓画在检材层
    cv2.drawContours(q_layer, diff_contours, -1, (255, 255, 0, 255), 2)  # BGRA 青

    def bgra_to_data_url(img):
        ok, buf = cv2.imencode(".png", img)
        if not ok:
            return ""
        return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode()

    print(f"  📊 SSIM={score:.4f}  差异像素={diff_ratio:.2%}  红色IoU={red_iou:.4f}")
    return dict(ssim=float(score), diff_ratio=float(diff_ratio),
                red_iou=float(red_iou), diff_img=diff,
                heatmap=heatmap, overlay=overlay_marked,
                overlay_r_img=bgra_to_data_url(ref_layer),
                overlay_q_img=bgra_to_data_url(q_layer),
                area_ratio=float(area_ratio))


def judge_authenticity(metrics: dict) -> dict:
    s = metrics["ssim"] * 0.5 + metrics["red_iou"] * 0.3 + (1-metrics["diff_ratio"]) * 0.2
    if s >= 0.85:
        verdict, level, color, icon = "真实", "高度一致",  "#2ecc71", "✅"
    elif s >= 0.70:
        verdict, level, color, icon = "存疑", "部分差异",  "#f39c12", "⚠️"
    else:
        verdict, level, color, icon = "疑似伪造", "差异显著", "#e74c3c", "❌"

    reasons = []
    if metrics["ssim"]       < 0.75: reasons.append(f"结构相似度偏低（SSIM={metrics['ssim']:.3f}，阈值0.75）")
    if metrics["diff_ratio"] > 0.20: reasons.append(f"差异像素比例偏高（{metrics['diff_ratio']:.1%}，阈值20%）")
    if metrics["red_iou"]    < 0.70: reasons.append(f"红色区域重叠度不足（IoU={metrics['red_iou']:.3f}，阈值0.70）")
    if metrics.get("area_ratio", 1) > 3:
        reasons.append(f"两章面积差异悬殊（{metrics['area_ratio']:.1f}x），比对结果仅供参考")
    if not reasons:
        reasons.append("各项指标均在正常范围内")

    return dict(score=s, verdict=verdict, level=level, color=color, icon=icon, reasons=reasons)


def ai_analysis(seal_q, seal_r, overlay) -> str:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return ""
    try:
        import anthropic as ant
        def b64(img):
            _, buf = cv2.imencode(".png", img)
            return base64.b64encode(buf.tobytes()).decode()
        client = ant.Anthropic()
        resp = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=800,
            messages=[{"role":"user","content":[
                {"type":"text","text":"你是印章鉴定专家。对比三张图（检材章、样本章、叠加图），从字体笔画、边框粗细、颜色深浅、整体形状、伪造迹象五个维度分析，150字以内给出结论。"},
                {"type":"image","source":{"type":"base64","media_type":"image/png","data":b64(seal_q)}},
                {"type":"image","source":{"type":"base64","media_type":"image/png","data":b64(seal_r)}},
                {"type":"image","source":{"type":"base64","media_type":"image/png","data":b64(overlay)}},
            ]}]
        )
        return resp.content[0].text
    except Exception as e:
        return f"（AI分析失败: {e}）"


# ─────────────────────────────────────────────────────────────
# HTML 报告
# ─────────────────────────────────────────────────────────────

STATUS_STYLE = {
    "ok":         ("✅", "#2ecc71", "正常"),
    "suspicious": ("🚨", "#e74c3c", "可疑"),
    "skipped":    ("⬜", "#95a5a6", "条件不足"),
}

def render_forgery_checks_html(checks: list) -> str:
    rows = []
    for c in checks:
        icon, color, label = STATUS_STYLE[c.status]
        img_html = ""
        if c.image:
            img_html = f'<img src="{pil_to_data_url(c.image)}" style="max-width:100%;border-radius:6px;margin-top:8px;">'
        rows.append(f"""
        <div class="check-row">
          <div class="check-header">
            <span class="check-badge" style="background:{color}">{icon} {label}</span>
            <span class="check-name">{c.name}</span>
          </div>
          <div class="check-detail">{c.detail}</div>
          {img_html}
        </div>""")
    return "\n".join(rows)


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>印章鉴定报告</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'PingFang SC','Microsoft YaHei',sans-serif;background:#f0f2f5;color:#2c3e50;
     max-width:960px;margin:0 auto;font-size:16px}}

.header{{background:linear-gradient(135deg,#1a1a2e,#0f3460);color:white;padding:24px 32px;
         display:flex;justify-content:space-between;align-items:center}}
.header h1{{font-size:24px;letter-spacing:2px}}
.header .meta{{font-size:14px;opacity:.7;text-align:right;line-height:1.8}}

.verdict{{background:{verdict_color};color:white;padding:14px 32px;
          display:flex;align-items:center;gap:12px;font-size:20px;font-weight:700}}
.verdict-score{{margin-left:auto;font-size:15px;font-weight:400;
               background:rgba(255,255,255,.2);padding:4px 14px;border-radius:20px}}

.grid4{{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:16px 32px}}
.cell{{background:white;border-radius:10px;padding:12px;box-shadow:0 2px 10px rgba(0,0,0,.07);
       display:flex;flex-direction:column}}
.cell-head{{display:flex;align-items:center;gap:6px;height:30px;flex-shrink:0}}
.cell-img{{flex:1;display:flex;align-items:center;justify-content:center;overflow:hidden;
           min-height:0}}
.cell-img img{{width:100%;max-height:100%;object-fit:contain;border-radius:6px;
              border:1px solid #ecf0f1;transition:transform 0.3s}}
.cell-foot{{flex-shrink:0;height:28px;display:flex;align-items:center;gap:14px;font-size:12px}}
.cell-foot-empty{{height:28px;flex-shrink:0}}
.ptitle{{font-size:14px;font-weight:700;color:#7f8c8d;text-transform:uppercase;
         letter-spacing:1.5px;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid #ecf0f1}}

.metrics-bar{{display:flex;gap:16px;padding:8px 32px;justify-content:center}}

.seal-label{{font-size:12px;font-weight:700;color:white;display:inline-block;
             padding:3px 8px;border-radius:3px;line-height:1}}
.seal-img{{width:100%;border-radius:6px;border:1px solid #ecf0f1;display:block;
           margin:0 auto 8px;transition:transform 0.3s}}
.overlay-wrap{{width:100%;position:relative;background:white}}
.overlay-wrap img{{width:100%;border-radius:6px;border:1px solid #ecf0f1}}
.seal-heatmap{{width:100%;margin:0 auto;display:block}}

.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:12px}}
.metric{{background:white;border-radius:8px;padding:12px;text-align:center;
         box-shadow:0 2px 10px rgba(0,0,0,.07);min-width:120px}}
.mv{{font-size:22px;font-weight:700}}
.ml{{font-size:13px;color:#95a5a6;margin-top:3px}}

.legend{{display:flex;gap:12px;font-size:12px;flex-wrap:wrap}}
.ld{{display:flex;align-items:center;gap:4px}}
.dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}

.footer{{padding:0 32px 30px}}
.abox{{background:white;border-radius:10px;padding:18px;
       box-shadow:0 2px 10px rgba(0,0,0,.07);margin-bottom:16px}}
.reason-item{{display:flex;gap:8px;padding:8px 0;border-bottom:1px solid #f5f5f5;font-size:15px}}
.reason-item:last-child{{border-bottom:none}}
.ai-text{{margin-top:12px;font-size:15px;line-height:1.8;color:#555;
          background:#f8f9fa;padding:12px;border-radius:6px}}

/* 文件真实性检测区块 */
.check-row{{padding:10px 0;border-bottom:1px solid #f0f0f0}}
.check-row:last-child{{border-bottom:none}}
.check-header{{display:flex;align-items:center;gap:8px;margin-bottom:4px}}
.check-badge{{font-size:12px;font-weight:700;padding:2px 8px;border-radius:10px;color:white}}
.check-name{{font-size:15px;font-weight:600}}
.check-detail{{font-size:13px;color:#666;line-height:1.6;padding-left:2px}}

.disclaimer{{text-align:center;padding:14px;font-size:13px;color:#bdc3c7}}

.rotate-btn{{display:inline-block;padding:3px 10px;background:#3498db;color:white;
            border:none;border-radius:3px;cursor:pointer;font-size:11px;line-height:1}}
.rotate-btn:hover{{background:#2980b9}}
.rot-deg{{display:inline-block;margin-left:4px;font-weight:700;min-width:2.5em;font-size:11px;line-height:1}}

.rerun-bar{{display:flex;align-items:center;gap:12px;padding:12px 32px;background:#f8f9fa;
            border-bottom:1px solid #ecf0f1;font-size:14px}}
.rerun-title{{font-weight:700;color:#2c3e50}}
.rerun-hint{{color:#7f8c8d}}
.rerun-code{{background:#2d3436;color:#dfe6e9;padding:6px 12px;border-radius:4px;
             font-family:'Consolas','Monaco',monospace;font-size:12px;
             white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0}}
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>🔍 印章真伪鉴定报告</h1>
    <div style="font-size:12px;opacity:.6;margin-top:4px">Seal Authenticity Verification</div>
  </div>
  <div class="meta">
    <div>检材：{query_name}</div><div>样本：{ref_name}</div>
    <div style="margin-top:4px">{timestamp}</div>
  </div>
</div>

<div class="verdict" style="background:{verdict_color}">
  <span>{verdict_icon}</span>
  <span>印章比对：{verdict}（{verdict_level}）</span>
  <span class="verdict-score">综合评分 {verdict_score:.1%}</span>
</div>

<div class="rerun-bar">
  <span class="rerun-title">🔄 方向调整</span>
  <span class="rerun-hint">如方向不正，点旋转按钮预览，确认后重跑：</span>
  <code id="rerunCmd" class="rerun-code"></code>
  <button class="rotate-btn" onclick="copyCmd()">📋 复制</button>
</div>

<div class="grid4">
  <!-- 左上：检材 -->
  <div class="cell">
    <div class="cell-head">
      <span class="seal-label" style="background:#e74c3c">检材（待鉴定）</span>
      <button class="rotate-btn" onclick="rotateQ()">↻72°</button>
      <span class="rot-deg" id="rotQ">0°</span>
    </div>
    <div class="cell-img"><img class="seal-q" src="{query_img}"></div>
    <div class="cell-foot-empty"></div>
  </div>
  <!-- 右上：叠加差异 -->
  <div class="cell">
    <div class="cell-head">
      <span class="seal-label" style="background:#8e44ad">叠加差异对比</span>
    </div>
    <div class="cell-img">
      <div class="overlay-wrap">
        <img class="overlay-r-layer" src="{overlay_r_img}">
        <img class="overlay-q-layer" src="{overlay_q_img}" style="position:absolute;top:0;left:0">
      </div>
    </div>
    <div class="cell-foot">
      <div class="ld"><div class="dot" style="background:#f44"></div><span>仅检材有</span></div>
      <div class="ld"><div class="dot" style="background:#44f"></div><span>仅样本有</span></div>
      <div class="ld"><div class="dot" style="background:#f0f"></div><span>重合</span></div>
      <div class="ld"><div class="dot" style="background:#0ff"></div><span>差异轮廓</span></div>
    </div>
  </div>
  <!-- 左下：样本 -->
  <div class="cell">
    <div class="cell-head">
      <span class="seal-label" style="background:#2980b9">样本（真实参考）</span>
      <button class="rotate-btn" onclick="rotateR()">↻72°</button>
      <span class="rot-deg" id="rotR">0°</span>
    </div>
    <div class="cell-img"><img class="seal-r" src="{ref_img}"></div>
    <div class="cell-foot-empty"></div>
  </div>
  <!-- 右下：热图 -->
  <div class="cell">
    <div class="cell-head">
      <span class="seal-label" style="background:#d35400">差异热图</span>
    </div>
    <div class="cell-img"><img class="seal-heatmap" src="{heatmap_img}"></div>
    <div class="cell-foot-empty"></div>
  </div>
</div>

<!-- 量化指标 -->
<div class="metrics-bar">
  <div class="metric"><div class="mv" style="color:{ssim_color}">{ssim:.3f}</div><div class="ml">SSIM 结构相似度</div></div>
  <div class="metric"><div class="mv" style="color:{iou_color}">{red_iou:.3f}</div><div class="ml">红色区域 IoU</div></div>
  <div class="metric"><div class="mv" style="color:{diff_color}">{diff_ratio:.1%}</div><div class="ml">差异像素比例</div></div>
</div>

<div class="footer">

  <!-- 印章比对分析 -->
  <div class="abox">
    <div class="ptitle">比对分析</div>
    <div>{reason_items}</div>
    {ai_section}
  </div>

  <!-- 文件真实性检测 -->
  <div class="abox">
    <div class="ptitle">文件真实性检测（PS / 抠图痕迹）</div>
    {forgery_checks_html}
  </div>

</div>

<div class="disclaimer">本报告由自动化图像分析生成，仅供参考，法律鉴定须委托专业机构出具。</div>
<script>
let rotQ = 0, rotR = 0;
const qStep = 0, rStep = 0;  // initial steps from CLI
const qPath = '{query_path}';
const rPath = '{ref_path}';
const sPath = '{script_path}';
const oPath = '{output_path}';
function updateCmd() {{
  const qs = rotQ / 72, rs = rotR / 72;
  let cmd = 'python "' + sPath + '" "' + qPath + '" "' + rPath + '" --output "' + oPath + '"';
  if (qs > 0) cmd += ' --rot-q ' + qs;
  if (rs > 0) cmd += ' --rot-r ' + rs;
  document.getElementById('rerunCmd').textContent = cmd;
}}
updateCmd();
function rotateQ() {{
  rotQ = (rotQ + 72) % 360;
  document.getElementById('rotQ').textContent = rotQ + '\\u00b0';
  document.querySelectorAll('.seal-q, .overlay-q-layer, .seal-heatmap').forEach(el => {{
    el.style.transform = 'rotate(' + rotQ + 'deg)';
  }});
  updateCmd();
}}
function rotateR() {{
  rotR = (rotR + 72) % 360;
  document.getElementById('rotR').textContent = rotR + '\\u00b0';
  document.querySelectorAll('.seal-r, .overlay-r-layer').forEach(el => {{
    el.style.transform = 'rotate(' + rotR + 'deg)';
  }});
  updateCmd();
}}
function copyCmd() {{
  const cmd = document.getElementById('rerunCmd').textContent;
  navigator.clipboard.writeText(cmd).then(() => {{
    alert('已复制命令到剪贴板');
  }});
}}
</script>
</body>
</html>"""


def generate_report(seal_q, seal_r, metrics, judgment, ai_text,
                    forgery_checks, query_name, ref_name, output_path,
                    query_path="", ref_path="", script_path=""):
    def c(val, hi=True, w=.75, b=.60):
        if hi:  return "#2ecc71" if val>=w else ("#f39c12" if val>=b else "#e74c3c")
        else:   return "#2ecc71" if val<=(1-w) else ("#f39c12" if val<=(1-b) else "#e74c3c")

    reason_html = "\n".join(
        f'<div class="reason-item"><span>{"✅" if len(judgment["reasons"])==1 and i==0 else "⚠️"}</span><span>{r}</span></div>'
        for i,r in enumerate(judgment["reasons"]))

    ai_html = (f'<div class="ptitle" style="margin-top:14px">AI 专家分析</div>'
               f'<div class="ai-text">{ai_text}</div>') if ai_text else ""

    html = HTML_TEMPLATE.format(
        query_name=query_name, ref_name=ref_name,
        query_path=query_path.replace("\\", "/"), ref_path=ref_path.replace("\\", "/"),
        script_path=script_path.replace("\\", "/"), output_path=output_path.replace("\\", "/"),
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        verdict_color=judgment["color"], verdict_icon=judgment["icon"],
        verdict=judgment["verdict"],     verdict_level=judgment["level"],
        verdict_score=judgment["score"],
        query_img=img_to_data_url(seal_q), ref_img=img_to_data_url(seal_r),
        overlay_img=img_to_data_url(metrics["overlay"]),
        overlay_r_img=metrics["overlay_r_img"],
        overlay_q_img=metrics["overlay_q_img"],
        heatmap_img=img_to_data_url(metrics["heatmap"]),
        ssim=metrics["ssim"], red_iou=metrics["red_iou"], diff_ratio=metrics["diff_ratio"],
        ssim_color=c(metrics["ssim"]),  iou_color=c(metrics["red_iou"]),
        diff_color=c(metrics["diff_ratio"], hi=False),
        reason_items=reason_html, ai_section=ai_html,
        forgery_checks_html=render_forgery_checks_html(forgery_checks),
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="印章真伪鉴定")
    parser.add_argument("query",  help="检材文件（jpg/png/pdf）")
    parser.add_argument("ref",    help="样本文件（jpg/png/pdf）")
    parser.add_argument("--output", default="seal_report.html")
    parser.add_argument("--rot-q", type=int, default=0,
                        help="检材额外旋转步数(0~4)，每步72°，用于人工消歧")
    parser.add_argument("--rot-r", type=int, default=0,
                        help="样本额外旋转步数(0~4)，每步72°，用于人工消歧")
    args = parser.parse_args()

    print("\n" + "="*52)
    print("  🔍 印章真伪鉴定系统 v3（星心配准）")
    print("="*52)

    print("\n[1/7] 加载文件...")
    img_q = load_image(args.query)
    img_r = load_image(args.ref)

    print("\n[2/7] 文件真实性检测（检材）...")
    forgery_checks = run_forgery_checks(args.query, img_q)

    print("\n[3/7] 检测印章区域...")
    seal_q_raw, _, _ = detect_seal(img_q)
    seal_r_raw, _, _ = detect_seal(img_r)

    print("\n[4/7] 星心归一化（各自旋转至星尖朝上）...")
    seal_q_aligned, seal_r = align_by_star(seal_q_raw, seal_r_raw,
                                               rot_q_steps=args.rot_q,
                                               rot_r_steps=args.rot_r)

    print("\n[5/7] 差异分析...")
    metrics  = analyze_difference(seal_q_aligned, seal_r)
    judgment = judge_authenticity(metrics)

    print("\n[6/7] AI分析 + 生成报告...")
    ai_text = ai_analysis(seal_q_aligned, seal_r, metrics["overlay"])
    if not ai_text:
        print("  ⏭  未设置ANTHROPIC_API_KEY，跳过AI分析")

    generate_report(
        seal_q=seal_q_aligned, seal_r=seal_r,
        metrics=metrics, judgment=judgment, ai_text=ai_text,
        forgery_checks=forgery_checks,
        query_name=Path(args.query).name,
        ref_name=Path(args.ref).name,
        output_path=args.output,
        query_path=args.query, ref_path=args.ref,
        script_path=str(Path(__file__).resolve()),
    )

    # 文件真实性汇总
    suspicious_checks = [c for c in forgery_checks if c.status == "suspicious"]

    print("\n" + "="*52)
    print(f"  {judgment['icon']} 印章比对：{judgment['verdict']}（{judgment['level']}）")
    print(f"  📊 综合评分：{judgment['score']:.1%}")
    if suspicious_checks:
        print(f"  🚨 文件真实性：{len(suspicious_checks)}项可疑")
        for c in suspicious_checks:
            print(f"     • {c.name}")
    else:
        print(f"  ✅ 文件真实性：无明显PS/抠图痕迹")
    print(f"  📄 报告：{args.output}")
    print("="*52 + "\n")


if __name__ == "__main__":
    main()