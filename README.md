# StegoVault 🔏

Hide **any file** (any type, any size) inside a normal-looking PNG image using LSB steganography. 100% offline — no data leaves your machine.

## Quick Start

```bash
pip install -r requirements.txt
streamlit run app.py
```

## How It Works

StegoVault replaces the **least significant bit** of each RGB color channel in the cover image with one bit of your hidden file. The change per pixel is only ±1 out of 255 — completely invisible to the human eye.

```
HIDE:   File → Compress → Pack header → Write bits into pixel LSBs → PNG
REVEAL: PNG → Read bits from pixel LSBs → Unpack header → Decompress → File
```

## Capacity

Capacity scales with the cover image size:

| Resolution | Capacity |
|---|---|
| 800 × 600 | ~180 KB |
| 1280 × 720 | ~346 KB |
| 1920 × 1080 | ~778 KB |
| 3840 × 2160 | ~3.1 MB |
| 7680 × 4320 | ~12.4 MB |

Formula: `max_bytes = (width × height × 3) ÷ 8 − overhead`

With zstandard compression, highly-compressible files (text, JSON, code) can be **3–10× larger** than the raw capacity above.

## Payload Format

```
[4B magic "STGV"][1B version][1B comp_id][2B fname_len][4B orig_size][4B comp_size]
[32B SHA-256 of original file]
[filename bytes]
[compressed file data]
```

## Important Notes

- **Always save the stego image as PNG** — JPEG recompression destroys the hidden bits
- The output image is visually identical to the input
- This is steganography, not encryption — for sensitive files, encrypt before hiding
- SHA-256 verification confirms perfect file recovery

## vs QR Codes

| | QR Code | StegoVault |
|---|---|---|
| Capacity | 2.9 KB max | Scales with image (MBs) |
| Looks like | A QR code | A normal photo |
| Scan with phone | ✅ | ❌ (software only) |
| File size limit | Very small | Essentially unlimited |

## License

MIT
