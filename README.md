# QR File Encoder/Decoder

Encode **any file** (MP3, PDF, images, videos — any size) into multiple QR codes and recover the original file perfectly. **100% offline.**

## Quick Start

```bash
pip install -r requirements.txt
streamlit run app.py
```

### System dependency

`pyzbar` requires the `libzbar` library:

| OS | Command |
|----|---------|
| Ubuntu/Debian | `sudo apt install libzbar0` |
| macOS | `brew install zbar` |
| Windows | Included with pip package |

## Features

### Generate (Encode)
- Upload any file type, any size
- Optional Zstandard compression
- Automatic splitting into scannable QR codes (~2,200 bytes payload each)
- Each QR includes UUID, chunk index, total count, and CRC32 checksum
- Download as ZIP or printable PDF
- Grid preview in app

### Decode (Reconstruct)
- Upload QR code images (bulk) or use webcam
- Scans in any order — automatic reassembly
- CRC32 per-chunk + SHA-256 full-file verification
- Progress tracking with missing chunk detection

### Settings
- Adjustable chunk size, QR module size, compression level
- Dark mode QR codes
- System capability info

## How Many QR Codes?

| File Size | After Compression (~15%) | QR Codes |
|-----------|-------------------------|----------|
| 1 MB | ~850 KB | ~390 |
| 10 MB | ~8.5 MB | ~3,865 |
| 50 MB | ~42.5 MB | ~19,320 |
| 100 MB | ~85 MB | ~38,640 |

## Architecture

Each QR code contains a binary payload:
```
[16B UUID][4B index][4B total][4B CRC32][payload data]
```

Chunk #0 is always metadata (JSON) with filename, sizes, compression method, and SHA-256 hash.

## License

MIT
