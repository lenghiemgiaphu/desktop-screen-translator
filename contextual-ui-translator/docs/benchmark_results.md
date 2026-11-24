### Experimental setup

- Date: 2026-11-24
- Platform: Linux 6.18.44-fc-v819, Python 3.12.3, Pillow 13.0.0
- Tesseract 5.3.4, OCR languages: `eng`
- Images: synthetic anti-aliased renders (not real screenshots)
- CJK case skipped (needs the `chi_sim` Tesseract pack and a CJK font)

### OCR accuracy and latency

| Case | Method | CER | WER | Latency (ms, median) |
| :-- | :-- | --: | --: | --: |
| Light-mode menu + shortcuts (12-14 px) | Raw Tesseract | 23.9% | 24.7% | 156 |
| Light-mode menu + shortcuts (12-14 px) | Prototype pipeline | 1.1% | 6.4% | 211 |
| Light-mode menu + shortcuts (12-14 px) | Adaptive pipeline | 0.0% | 0.4% | 287 |
| Dark-mode menu + shortcuts (12-14 px) | Raw Tesseract | 28.4% | 33.7% | 255 |
| Dark-mode menu + shortcuts (12-14 px) | Prototype pipeline | 1.4% | 8.8% | 386 |
| Dark-mode menu + shortcuts (12-14 px) | Adaptive pipeline | 0.1% | 0.6% | 361 |
| Dark-mode IDE labels / code (monospace) | Raw Tesseract | 1.7% | 14.4% | 259 |
| Dark-mode IDE labels / code (monospace) | Prototype pipeline | 0.8% | 10.5% | 388 |
| Dark-mode IDE labels / code (monospace) | Adaptive pipeline | 0.7% | 10.0% | 281 |
| Small 10 px paragraph (3-4 lines) | Raw Tesseract | 1.2% | 7.0% | 146 |
| Small 10 px paragraph (3-4 lines) | Prototype pipeline | 0.0% | 0.0% | 216 |
| Small 10 px paragraph (3-4 lines) | Adaptive pipeline | 0.0% | 0.0% | 218 |
| 14 px wrapped paragraph, light + dark | Raw Tesseract | 0.2% | 0.8% | 190 |
| 14 px wrapped paragraph, light + dark | Prototype pipeline | 0.0% | 0.0% | 293 |
| 14 px wrapped paragraph, light + dark | Adaptive pipeline | 0.0% | 0.0% | 222 |

### Summary (mean over cases)

| Subset | Method | CER | WER | Latency (ms) |
| :-- | :-- | --: | --: | --: |
| All cases | Raw Tesseract | 11.1% | 16.1% | 201 |
| All cases | Prototype pipeline | 0.7% | 5.1% | 299 |
| All cases | Adaptive pipeline | 0.2% | 2.2% | 274 |
| All cases | **Error reduction of adaptive vs raw / vs prototype** | 98% / 73% | 86% / 57% | |
| Dark-mode cases | Raw Tesseract | 15.0% | 24.0% | 257 |
| Dark-mode cases | Prototype pipeline | 1.1% | 9.7% | 387 |
| Dark-mode cases | Adaptive pipeline | 0.4% | 5.3% | 321 |
| Dark-mode cases | **Error reduction of adaptive vs raw / vs prototype** | 97% / 62% | 78% / 45% | |

### Structure: output blocks == expected blocks

Menus should come out as one block per item (shortcut attached); a wrapped paragraph as one block.

| Case | Raw | Prototype | Adaptive |
| :-- | --: | --: | --: |
| Light-mode menu + shortcuts (12-14 px) | 45.0% | 100.0% | 100.0% |
| Dark-mode menu + shortcuts (12-14 px) | 40.0% | 100.0% | 100.0% |
| Dark-mode IDE labels / code (monospace) | 100.0% | 100.0% | 90.0% |
| Small 10 px paragraph (3-4 lines) | 0.0% | 0.0% | 100.0% |
| 14 px wrapped paragraph, light + dark | 0.0% | 0.0% | 100.0% |

_20 generated samples per case, seed 7. Error reduction = (old - new) / old; negative means the adaptive pipeline is worse._
