#!/usr/bin/env python3
"""카카오톡 이모티콘 원본 이미지를 네이버 OGQ마켓 규격으로 변환한다.

OGQ마켓 규격
  - 메인 이미지 1개  : 240x240
  - 스티커 이미지 24개: 740x640
  - 탭 이미지 1개    : 96x74
  - 공통: PNG, 투명 배경, 72dpi 이상, 파일당 1MB 이하

사용법
  python3 scripts/convert_kakao_to_ogq.py
  python3 scripts/convert_kakao_to_ogq.py --src "이모티콘-가지미" --out ogq_market

새 원본 이미지가 폴더에 추가되면 이 스크립트를 다시 실행하기만 하면
동일한 규칙으로 배경 제거 + 리사이즈 + 파일 정리가 다시 수행된다.

필요 패키지: pillow, numpy, scipy (scripts/requirements.txt 참고)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import label as cc_label

# ---------------------------------------------------------------------------
# 규격 상수
# ---------------------------------------------------------------------------
MAIN_SIZE = (240, 240)
STICKER_SIZE = (740, 640)
TAB_SIZE = (96, 74)
STICKER_COUNT = 24
DPI = (72, 72)
MAX_BYTES = 1024 * 1024  # 1MB
MARGIN_RATIO = 0.06  # 캔버스 대비 여백 비율 (양쪽 합산 아님, 편측)

IMAGE_EXTS = {".png", ".jpg", ".jpeg"}
EXCLUDE_KEYWORDS = ("preview", "grid")  # 개별 이모티콘이 아닌 부속 이미지 제외
ORDER_REF_PATTERN = re.compile(r"^(\d+)_(.+)\.png$", re.IGNORECASE)


def strip_image_ext(name: str) -> str:
    """'파일.png.png' 처럼 확장자가 중복돼도 전부 제거한다."""
    while True:
        stem, ext = Path(name).stem, Path(name).suffix.lower()
        if ext in IMAGE_EXTS and stem != name:
            name = stem
        else:
            break
    return name


def discover_sources(src_dir: Path) -> list[Path]:
    files = []
    for p in sorted(src_dir.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in IMAGE_EXTS:
            continue
        lowered = p.name.lower()
        if any(k in lowered for k in EXCLUDE_KEYWORDS):
            continue
        files.append(p)
    return files


def build_order_map(order_ref_dir: Path) -> dict[str, int]:
    """기존에 정리된 순번(예: output/body_360/01_xxx.png)을 정렬 기준으로 재사용한다."""
    order_map: dict[str, int] = {}
    if not order_ref_dir.is_dir():
        return order_map
    for p in order_ref_dir.iterdir():
        m = ORDER_REF_PATTERN.match(p.name)
        if m:
            order_map[m.group(2)] = int(m.group(1))
    return order_map


def sort_sources(files: list[Path], order_map: dict[str, int]) -> list[Path]:
    def key(p: Path):
        label = strip_image_ext(p.name)
        if label in order_map:
            return (0, order_map[label], label)
        if label.strip() in order_map:
            return (0, order_map[label.strip()], label)
        return (1, 0, label)  # 순번 참조가 없는 새 이미지는 뒤쪽에 알파벳순으로 배치

    return sorted(files, key=key)


# ---------------------------------------------------------------------------
# 배경 제거
# ---------------------------------------------------------------------------
def remove_background(
    im: Image.Image, white_threshold: float = 32.0, feather: float = 1.6
) -> Image.Image:
    """테두리에서 시작해 흰색/거의 흰색으로 이어진 영역만 배경으로 간주해 투명화한다.

    - 캐릭터 내부의 흰색(눈, 하이라이트 등)은 테두리와 연결돼 있지 않으므로 보존된다.
    - 경계에 feather(가우시안 블러)를 적용해 계단 현상과 흰 테두리를 줄인다.
    - 색 디콘타미네이션으로 반투명 경계의 흰색 번짐을 제거한다.
    """
    rgb = np.asarray(im.convert("RGB"), dtype=np.float32)
    h, w, _ = rgb.shape

    dist_from_white = np.sqrt(((rgb - 255.0) ** 2).sum(axis=2))
    near_white = dist_from_white < white_threshold

    labels, _ = cc_label(near_white)
    border_labels = set(labels[0, :].tolist()) | set(labels[-1, :].tolist())
    border_labels |= set(labels[:, 0].tolist()) | set(labels[:, -1].tolist())
    border_labels.discard(0)

    bg_mask = np.isin(labels, list(border_labels))

    alpha = np.where(bg_mask, 0.0, 255.0).astype(np.float32)

    if feather > 0:
        from PIL import ImageFilter

        alpha_img = Image.fromarray(alpha.astype(np.uint8), mode="L").filter(
            ImageFilter.GaussianBlur(feather)
        )
        alpha = np.asarray(alpha_img, dtype=np.float32)

    alpha_norm = np.clip(alpha / 255.0, 0.0, 1.0)[..., None]
    decontaminated = (rgb - (1 - alpha_norm) * 255.0) / np.clip(alpha_norm, 1e-3, 1.0)
    decontaminated = np.clip(decontaminated, 0, 255)

    out = np.concatenate([decontaminated, alpha[..., None]], axis=2).astype(np.uint8)
    return Image.fromarray(out, mode="RGBA")


def trim_to_content(im: Image.Image, pad: int = 2) -> Image.Image:
    alpha = np.asarray(im.split()[-1])
    ys, xs = np.where(alpha > 8)
    if len(xs) == 0:
        return im
    x0, x1 = max(xs.min() - pad, 0), min(xs.max() + pad + 1, im.width)
    y0, y1 = max(ys.min() - pad, 0), min(ys.max() + pad + 1, im.height)
    return im.crop((x0, y0, x1, y1))


def fit_to_canvas(im: Image.Image, size: tuple[int, int], margin_ratio: float) -> Image.Image:
    target_w, target_h = size
    box_w = target_w * (1 - 2 * margin_ratio)
    box_h = target_h * (1 - 2 * margin_ratio)

    scale = min(box_w / im.width, box_h / im.height)
    new_w = max(1, round(im.width * scale))
    new_h = max(1, round(im.height * scale))
    resized = im.resize((new_w, new_h), Image.LANCZOS)

    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    off_x = (target_w - new_w) // 2
    off_y = (target_h - new_h) // 2
    canvas.paste(resized, (off_x, off_y), resized)
    return canvas


def save_png(im: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, format="PNG", optimize=True, dpi=DPI)

    if path.stat().st_size > MAX_BYTES:
        # 용량 초과 시 팔레트 압축으로 1MB 이하로 재저장 (투명도는 유지)
        quantized = im.quantize(colors=256, method=Image.FASTOCTREE)
        quantized.save(path, format="PNG", optimize=True, dpi=DPI)

    size = path.stat().st_size
    if size > MAX_BYTES:
        print(f"  ! 경고: {path.name} 파일 용량이 {size/1024:.0f}KB로 1MB를 초과합니다.")


def process_one(src_path: Path, size: tuple[int, int], margin_ratio: float) -> Image.Image:
    im = Image.open(src_path)
    if im.mode == "RGBA":
        # 이미 배경 제거된 소스(예: 이전 가공물)는 그대로 활용
        removed = im
    else:
        removed = remove_background(im)
    trimmed = trim_to_content(removed)
    return fit_to_canvas(trimmed, size, margin_ratio)


# ---------------------------------------------------------------------------
# 메인 파이프라인
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", default="이모티콘-가지미", help="카카오 원본 이미지 폴더")
    parser.add_argument("--out", default="ogq_market", help="OGQ마켓 규격 결과물 폴더")
    parser.add_argument(
        "--order-ref",
        default=None,
        help="정렬 기준으로 참고할 기존 번호 매김 폴더 (기본: <src>/output/body_360)",
    )
    parser.add_argument("--main-source", default=None, help="메인 이미지로 쓸 원본 파일명(일부 문자열 매칭)")
    parser.add_argument("--tab-source", default=None, help="탭 이미지로 쓸 원본 파일명(일부 문자열 매칭)")
    parser.add_argument("--margin", type=float, default=MARGIN_RATIO, help="캔버스 여백 비율 (기본 0.06)")
    args = parser.parse_args()

    src_dir = Path(args.src)
    out_dir = Path(args.out)
    order_ref_dir = Path(args.order_ref) if args.order_ref else src_dir / "output" / "body_360"

    if not src_dir.is_dir():
        print(f"원본 폴더를 찾을 수 없습니다: {src_dir}", file=sys.stderr)
        return 1

    sources = discover_sources(src_dir)
    if not sources:
        print(f"{src_dir} 에서 처리할 이미지를 찾지 못했습니다.", file=sys.stderr)
        return 1

    order_map = build_order_map(order_ref_dir)
    sources = sort_sources(sources, order_map)

    print(f"발견된 원본 이미지: {len(sources)}개")

    sticker_sources = sources[:STICKER_COUNT]
    extra_sources = sources[STICKER_COUNT:]

    def pick_source(pattern: str | None, fallback: Path) -> Path:
        if not pattern:
            return fallback
        for p in sources:
            if pattern in p.name:
                return p
        print(f"  ! '{pattern}' 과 일치하는 원본을 찾지 못해 기본값을 사용합니다: {fallback.name}")
        return fallback

    main_source = pick_source(args.main_source, sticker_sources[0])
    tab_source = pick_source(args.tab_source, sticker_sources[0])

    # --- 스티커 24개 ---
    stickers_dir = out_dir / "stickers"
    print(f"\n[스티커] {STICKER_SIZE[0]}x{STICKER_SIZE[1]} x {len(sticker_sources)}개 생성")
    for idx, src in enumerate(sticker_sources, start=1):
        result = process_one(src, STICKER_SIZE, args.margin)
        out_path = stickers_dir / f"{idx:02d}.png"
        save_png(result, out_path)
        print(f"  {idx:02d}.png  <-  {src.name}")

    # --- 메인 이미지 ---
    print(f"\n[메인] {MAIN_SIZE[0]}x{MAIN_SIZE[1]}  <-  {main_source.name}")
    main_img = process_one(main_source, MAIN_SIZE, args.margin)
    save_png(main_img, out_dir / "main" / "main.png")

    # --- 탭 이미지 ---
    print(f"[탭]   {TAB_SIZE[0]}x{TAB_SIZE[1]}  <-  {tab_source.name}")
    tab_img = process_one(tab_source, TAB_SIZE, args.margin)
    save_png(tab_img, out_dir / "tab" / "tab.png")

    # --- 24개 초과분은 후보로 별도 보관 ---
    if extra_sources:
        extra_dir = out_dir / "_extra_candidates"
        print(f"\n[여분 후보] 24개를 초과한 {len(extra_sources)}개는 스티커 규격으로만 변환해 보관합니다.")
        for idx, src in enumerate(extra_sources, start=1):
            result = process_one(src, STICKER_SIZE, args.margin)
            out_path = extra_dir / f"extra_{idx:02d}.png"
            save_png(result, out_path)
            print(f"  extra_{idx:02d}.png  <-  {src.name}")

    # --- 부족분 안내 ---
    shortage = STICKER_COUNT - len(sticker_sources)
    print("\n" + "=" * 50)
    if shortage > 0:
        print(f"스티커 원본이 {len(sticker_sources)}개뿐입니다. OGQ마켓 규격(24개)까지 {shortage}개 더 필요합니다.")
    else:
        print(f"스티커 원본 {len(sources)}개 중 24개를 사용했습니다.")
        if extra_sources:
            print(f"나머지 {len(extra_sources)}개는 '_extra_candidates' 폴더에 후보로 저장했습니다 (제출용 아님).")
    print(f"결과물 위치: {out_dir.resolve()}")
    print("=" * 50)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
