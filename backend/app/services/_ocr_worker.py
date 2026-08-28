"""OCR Worker 子进程脚本：在独立进程中运行 RapidOCR。

通过子进程隔离，避免 RapidOCR / ONNX Runtime 的底层崩溃影响主进程。
输出格式：JSON 序列化的文本块列表 [{text, bbox, confidence}, ...]

使用 RapidOCR（基于 ONNX Runtime）替代 PaddleOCR 的原因：
- PaddlePaddle 3.x 在 aarch64（Apple Silicon Docker）上存在 C++ 段错误
- RapidOCR 是 ONNX Runtime 版本，跨架构稳定，模型更小、启动更快
- API 与 PaddleOCR 相似
"""
import json
import sys
from pathlib import Path


def main():
    img_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not img_path or not Path(img_path).exists():
        print(json.dumps({"error": f"图片不存在: {img_path}"}))
        sys.exit(1)

    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        print(json.dumps({"error": "RapidOCR 未安装"}))
        sys.exit(1)

    # 配置环境：限制线程数，减少资源冲突
    import os
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")

    # 初始化 OCR 引擎
    try:
        engine = RapidOCR()
    except Exception as e:
        print(json.dumps({"error": f"OCR 引擎初始化失败: {str(e)[:300]}"}))
        sys.exit(1)

    # 执行识别
    try:
        # RapidOCR 返回 (results, elapse)
        # results: List[[box, text, conf]] 或 None
        result, _elapse = engine(img_path)
    except Exception as e:
        print(json.dumps({"error": f"OCR 识别失败: {str(e)[:300]}"}))
        sys.exit(1)

    # 解析结果
    items = []
    if not result:
        print(json.dumps(items, ensure_ascii=False))
        return

    for item in result:
        try:
            # RapidOCR 的输出格式: [box, text, conf]
            # box: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            box, text, conf = item[0], item[1], item[2]
            # box 转 float
            bbox = [[float(p[0]), float(p[1])] for p in box]
            items.append({
                "text": str(text),
                "bbox": bbox,
                "confidence": float(conf),
            })
        except Exception as e:
            # 单条解析失败不影响其他
            sys.stderr.write(f"解析单条结果失败: {e}\n")
            continue

    print(json.dumps(items, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # 任何未捕获异常都转为 JSON 输出
        print(json.dumps({"error": "未预期异常: " + str(e)[:300]}))
        sys.exit(1)
