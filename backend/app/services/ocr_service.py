"""OCR 服务：使用 RapidOCR 提取发票文字及坐标位置。

采用子进程隔离策略：RapidOCR 在独立进程中运行，避免底层崩溃导致
主进程被影响，并提供稳定的回退机制。
"""
import json
import logging
import sys
import subprocess
from pathlib import Path
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# 状态标志
_ocr_available = None
_ocr_error_logged = False


def check_paddleocr() -> bool:
    """检查 RapidOCR 是否可用（通过子进程测试）。

    函数名沿用旧名以保持调用方兼容；内部已切换到 RapidOCR。
    """
    global _ocr_available, _ocr_error_logged
    if _ocr_available is not None:
        return _ocr_available

    try:
        # 通过子进程测试导入，避免主进程污染
        result = subprocess.run(
            [sys.executable, "-c", "import rapidocr_onnxruntime; print(rapidocr_onnxruntime.__version__ if hasattr(rapidocr_onnxruntime, '__version__') else 'ok')"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            _ocr_available = True
            logger.info("RapidOCR 子进程测试通过: %s", result.stdout.strip())
        else:
            _ocr_available = False
            if not _ocr_error_logged:
                logger.warning("RapidOCR 不可用: %s", result.stderr[:200])
                _ocr_error_logged = True
    except Exception as e:
        _ocr_available = False
        if not _ocr_error_logged:
            logger.warning("RapidOCR 检测异常: %s", e)
            _ocr_error_logged = True

    return _ocr_available


def extract_text_with_positions(img_path: Path) -> List[Dict[str, Any]]:
    """提取图片中的文字及位置信息（子进程隔离模式）。

    Returns:
        [{"text": "发票代码", "bbox": [...], "confidence": 0.98}, ...]
    """
    if not check_paddleocr():
        raise RuntimeError("PaddleOCR 不可用")

    # 构造子进程脚本路径
    worker_script = Path(__file__).parent / "_ocr_worker.py"

    try:
        # 调用子进程执行 OCR
        result = subprocess.run(
            [sys.executable, str(worker_script), str(img_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            logger.warning("OCR 子进程失败: %s", result.stderr[:300])
            return []

        # 解析 JSON 输出
        items = json.loads(result.stdout)
        logger.info("OCR 子进程提取到 %d 个文本块", len(items))
        return items

    except subprocess.TimeoutExpired:
        logger.warning("OCR 子进程超时（120s）")
        return []
    except json.JSONDecodeError as e:
        logger.warning("OCR 子进程输出解析失败: %s", e)
        return []
    except Exception as e:
        logger.warning("OCR 调用异常: %s", e)
        return []


def get_image_size(img_path: Path) -> tuple:
    """获取图片尺寸。"""
    from PIL import Image
    with Image.open(img_path) as img:
        return img.size  # (width, height)


def group_by_region(
    items: List[Dict[str, Any]],
    img_width: int,
    img_height: int
) -> Dict[str, List[Dict[str, Any]]]:
    """根据位置将文本块分组到不同区域。

    区域划分（基于标准发票布局）：
    - top_right: 右上角 - 发票代码、发票号码、开票日期
    - center: 中部 - 金额、商品明细
    - bottom: 底部 - 销售方、购买方、备注
    - left: 左侧
    """
    regions = {
        "top_right": [],
        "center": [],
        "bottom": [],
        "left": [],
    }

    for item in items:
        bbox = item.get("bbox", [])
        if not bbox or len(bbox) < 4:
            continue

        # 计算中心点
        try:
            center_x = sum(p[0] for p in bbox) / len(bbox)
            center_y = sum(p[1] for p in bbox) / len(bbox)
        except (TypeError, IndexError):
            continue

        # 右上角区域：x > 50% 且 y < 30%
        if center_x > img_width * 0.5 and center_y < img_height * 0.3:
            regions["top_right"].append(item)
        elif center_y > img_height * 0.7:
            regions["bottom"].append(item)
        elif center_x < img_width * 0.4:
            regions["left"].append(item)
        else:
            regions["center"].append(item)

    return regions


def format_item_for_prompt(item: Dict[str, Any], img_width: int, img_height: int) -> str:
    """格式化 OCR 项为带位置信息的文本，供 LLM 使用。"""
    bbox = item.get("bbox", [])
    if not bbox or len(bbox) < 4:
        return f"  \"{item.get('text', '')}\" (置信度={item.get('confidence', 0):.2f})"

    # 中心点坐标（按图片尺寸归一化到 0-1000 范围，便于 LLM 理解）
    cx = int(sum(p[0] for p in bbox) / len(bbox) / img_width * 1000)
    cy = int(sum(p[1] for p in bbox) / len(bbox) / img_height * 1000)
    return f"  位置(x={cx},y={cy}): \"{item.get('text', '')}\" (置信度={item.get('confidence', 0):.2f})"


def build_ocr_context(img_path: Path) -> str:
    """构建 OCR 上下文文本，喂给 LLM。"""
    items = extract_text_with_positions(img_path)
    if not items:
        return ""

    w, h = get_image_size(img_path)
    regions = group_by_region(items, w, h)

    lines = []
    lines.append("【右上角区域 - 发票代码/号码/日期】")
    for item in regions["top_right"]:
        lines.append(format_item_for_prompt(item, w, h))

    lines.append("\n【中部区域 - 金额/明细】")
    for item in regions["center"]:
        lines.append(format_item_for_prompt(item, w, h))

    lines.append("\n【底部区域 - 销售方/购买方/备注】")
    for item in regions["bottom"]:
        lines.append(format_item_for_prompt(item, w, h))

    if regions["left"]:
        lines.append("\n【左侧区域】")
        for item in regions["left"]:
            lines.append(format_item_for_prompt(item, w, h))

    context = "\n".join(lines)
    logger.info("OCR 上下文构建完成，总字符数=%d", len(context))
    return context
