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
def _kmeans(points: np.ndarray, k: int, iters: int = 10, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    k = min(k, len(points))
    centers = points[rng.choice(len(points), size=k, replace=False)].copy()
    for _ in range(iters):
        dist = np.linalg.norm(points[:, None, :] - centers[None, :, :], axis=2)
        assign = dist.argmin(axis=1)
        for j in range(k):
            cluster = points[assign == j]
            if len(cluster):
                centers[j] = cluster.mean(axis=0)
    return centers


def remove_background(
    im: Image.Image,
    ring_px: int = 4,
    n_clusters: int = 6,
    color_threshold: float = 26.0,
    feather: float = 1.6,
) -> Image.Image:
    """이미지 테두리 색상을 자동으로 감지해, 테두리와 연결된 영역만 배경으로 간주해 투명화한다.

    원본마다 배경이 흰색/회색/연한 체크무늬/미세한 그라데이션 등으로 제각각이라 고정된
    흰색 기준으로는 일부가 전혀 투명화되지 않는 문제가 있었다. 대신 아주 얇은 테두리 링
    (기본 4px)의 실제 색상들을 몇 개의 대표색(클러스터)으로 요약하고, 그 대표색과
    색상 거리가 가까운 픽셀만 배경 후보로 삼는다. 팔레트 양자화 후 정확히 같은 색상
    인덱스만 매칭하는 방식은 배경이 다색 그라데이션/디더링일 때 일부 중간 색조를
    놓쳐 배경 전체가 테두리와 끊어지는 문제가 있었는데, 색상 거리 기반 매칭은 그런
    중간 색조도 자연스럽게 포함한다.

    링을 넓게 잡으면 테두리 근처의 반짝이 효과 같은 장식 요소 색상까지 배경 후보에
    섞여 들어가 캐릭터 본체가 잘못 지워지므로, 실제 배경만 좁게 샘플링한다.

    - 캐릭터 내부의 동일 색상(눈, 하이라이트 등)은 테두리와 연결돼 있지 않으므로 보존된다.
    - 경계에 feather(가우시안 블러)를 적용해 계단 현상과 배경색 번짐을 줄인다.
    - 색 디콘타미네이션으로 반투명 경계에 남는 배경색 번짐을 제거한다.
    """
    rgb_im = im.convert("RGB")
    rgb = np.asarray(rgb_im, dtype=np.float32)
    h, w, _ = rgb.shape

    ring = np.concatenate(
        [
            rgb[:ring_px, :].reshape(-1, 3),
            rgb[-ring_px:, :].reshape(-1, 3),
            rgb[:, :ring_px].reshape(-1, 3),
            rgb[:, -ring_px:].reshape(-1, 3),
        ]
    )
    sample_cap = 4000
    if len(ring) > sample_cap:
        ring = ring[np.random.default_rng(0).choice(len(ring), sample_cap, replace=False)]

    centers = _kmeans(ring, n_clusters)
    dist_to_centers = np.linalg.norm(rgb[:, :, None, :] - centers[None, None, :, :], axis=3)
    min_dist = dist_to_centers.min(axis=2)
    bg_candidate = min_dist < color_threshold

    labels, _ = cc_label(bg_candidate)
    border_labels = set(labels[0, :].tolist()) | set(labels[-1, :].tolist())
    border_labels |= set(labels[:, 0].tolist()) | set(labels[:, -1].tolist())
    border_labels.discard(0)

    bg_mask = np.isin(labels, list(border_labels))
    bg_color = rgb[bg_mask].mean(axis=0) if bg_mask.any() else np.array([255.0, 255.0, 255.0])

    alpha = np.where(bg_mask, 0.0, 255.0).astype(np.float32)

    if feather > 0:
        from PIL import ImageFilter

        alpha_img = Image.fromarray(alpha.astype(np.uint8), mode="L").filter(
            ImageFilter.GaussianBlur(feather)
        )
        alpha = np.asarray(alpha_img, dtype=np.float32)

    alpha_norm = np.clip(alpha / 255.0, 0.0, 1.0)[..., None]
    decontaminated = (rgb - (1 - alpha_norm) * bg_color) / np.clip(alpha_norm, 1e-3, 1.0)
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
# 톤(밝기) 정규화
# ---------------------------------------------------------------------------
def masked_value_mean(im: Image.Image) -> float:
    """불투명한(캐릭터) 영역만 골라 HSV의 V(명도) 평균을 구한다."""
    arr = np.asarray(im.convert("RGBA"))
    alpha = arr[:, :, 3]
    mask = alpha > 200
    if not mask.any():
        return 128.0
    hsv = np.asarray(im.convert("RGB").convert("HSV"))
    return float(hsv[:, :, 2][mask].mean())


def _apply_gamma(im: Image.Image, gamma: float) -> Image.Image:
    arr = np.asarray(im.convert("RGBA"), dtype=np.float32)
    rgb = 255.0 * np.power(np.clip(arr[:, :, :3], 0, 255) / 255.0, gamma)
    out = np.concatenate([rgb, arr[:, :, 3:4]], axis=2).astype(np.uint8)
    return Image.fromarray(out, mode="RGBA")


def brighten_to_target(im: Image.Image, target_v: float, min_gamma: float = 0.3) -> Image.Image:
    """이미지가 목표 명도보다 어두우면 감마 보정으로 밝게 끌어올린다(어두운 쪽으로는 조정하지 않음).

    단순 곱셈 스케일링은 이미 밝은 하이라이트(고글 반사광 등)가 255에서 바로 클리핑돼
    평균 명도가 목표까지 못 올라가는 경우가 많아, 감마 커브를 이진 탐색으로 맞춘다.
    """
    current_v = masked_value_mean(im)
    if current_v <= 1.0 or current_v >= target_v:
        return im
    lo, hi = min_gamma, 1.0
    gamma = 1.0
    for _ in range(10):
        gamma = (lo + hi) / 2
        v = masked_value_mean(_apply_gamma(im, gamma))
        if v < target_v:
            hi = gamma
        else:
            lo = gamma
    return _apply_gamma(im, gamma)


def normalize_tone(images: list[Image.Image], percentile: float = 75.0) -> list[Image.Image]:
    """세트 전체의 밝기를 맞춘다. 이미 밝은 상위 percentile 그룹 수준을 목표로,
    그보다 어두운 이미지들만 그 밝기까지 끌어올려 톤을 통일한다."""
    v_means = [masked_value_mean(im) for im in images]
    target_v = float(np.percentile(v_means, percentile))
    return [brighten_to_target(im, target_v) for im in images]


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
    parser.add_argument(
        "--tone-percentile",
        type=float,
        default=75.0,
        help="세트 밝기 통일 기준 백분위수 (기본 75: 상위 25%% 밝기를 목표로 어두운 이미지를 끌어올림)",
    )
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

    # --- 1단계: 배경 제거 + 리사이즈 (아직 저장하지 않음) ---
    print(f"\n[변환] 스티커 {len(sticker_sources)}개 + 메인 1개 + 탭 1개 + 여분 {len(extra_sources)}개 처리 중...")
    sticker_imgs = [process_one(src, STICKER_SIZE, args.margin) for src in sticker_sources]
    main_img = process_one(main_source, MAIN_SIZE, args.margin)
    tab_img = process_one(tab_source, TAB_SIZE, args.margin)
    extra_imgs = [process_one(src, STICKER_SIZE, args.margin) for src in extra_sources]

    # --- 2단계: 세트 전체 밝기 톤 통일 (어두운 것들만 밝은 쪽 기준으로 끌어올림) ---
    all_imgs = sticker_imgs + [main_img, tab_img] + extra_imgs
    normalized = normalize_tone(all_imgs, percentile=args.tone_percentile)
    n_stickers = len(sticker_imgs)
    sticker_imgs = normalized[:n_stickers]
    main_img = normalized[n_stickers]
    tab_img = normalized[n_stickers + 1]
    extra_imgs = normalized[n_stickers + 2 :]

    # --- 3단계: 저장 ---
    stickers_dir = out_dir / "stickers"
    for idx, (src, result) in enumerate(zip(sticker_sources, sticker_imgs), start=1):
        out_path = stickers_dir / f"{idx:02d}.png"
        save_png(result, out_path)
        print(f"  {idx:02d}.png  <-  {src.name}")

    print(f"\n[메인] {MAIN_SIZE[0]}x{MAIN_SIZE[1]}  <-  {main_source.name}")
    save_png(main_img, out_dir / "main" / "main.png")

    print(f"[탭]   {TAB_SIZE[0]}x{TAB_SIZE[1]}  <-  {tab_source.name}")
    save_png(tab_img, out_dir / "tab" / "tab.png")

    if extra_sources:
        extra_dir = out_dir / "_extra_candidates"
        print(f"\n[여분 후보] 24개를 초과한 {len(extra_sources)}개는 스티커 규격으로만 변환해 보관합니다.")
        for idx, (src, result) in enumerate(zip(extra_sources, extra_imgs), start=1):
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
