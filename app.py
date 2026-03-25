"""
QR File Encoder/Decoder — 100% Offline Desktop App
Encode ANY file into multiple QR codes. Decode them back perfectly.
Run: streamlit run app.py
"""

import streamlit as st
import qrcode
import qrcode.constants
from PIL import Image
import io
import os
import uuid
import struct
import hashlib
import zlib
import math
import zipfile
import tempfile
import json
import base64
from datetime import datetime

try:
    import zstandard as zstd
    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False

try:
    from pyzbar.pyzbar import decode as pyzbar_decode
    HAS_PYZBAR = True
except ImportError:
    HAS_PYZBAR = False

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    from reportlab.lib.pagesizes import A4, letter
    from reportlab.lib.units import mm, inch
    from reportlab.pdfgen import canvas as pdf_canvas
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

import numpy as np

# ─── Constants ───────────────────────────────────────────────────────
HEADER_SIZE = 28          # 16 (uuid) + 4 (index) + 4 (total) + 4 (crc32)
MAX_QR_BINARY = 2953      # QR v40-L max binary bytes
# Safe payload = max QR capacity minus the header we prepend to every chunk
MAX_PAYLOAD = MAX_QR_BINARY - HEADER_SIZE   # 2925 bytes
DEFAULT_CHUNK = 2200      # default payload per QR (well under MAX_PAYLOAD)
MAGIC = b"QRFE"           # 4-byte magic for our format

# ─── Helpers ─────────────────────────────────────────────────────────

def compress_data(data: bytes, level: int = 3) -> tuple[bytes, str]:
    """Compress data with zstd (preferred) or zlib. Returns (compressed, method)."""
    if HAS_ZSTD:
        cctx = zstd.ZstdCompressor(level=level)
        compressed = cctx.compress(data)
        return compressed, "zstd"
    else:
        compressed = zlib.compress(data, level)
        return compressed, "zlib"

def decompress_data(data: bytes, method: str) -> bytes:
    if method == "zstd" and HAS_ZSTD:
        dctx = zstd.ZstdDecompressor()
        return dctx.decompress(data, max_output_size=500_000_000)
    elif method == "zlib":
        return zlib.decompress(data)
    else:
        raise ValueError(f"Unknown compression: {method}")

def build_metadata_chunk(file_uuid: bytes, filename: str, total_chunks: int,
                         original_size: int, compressed_size: int,
                         compression: str, file_hash: str) -> bytes:
    """Chunk index 0 = metadata (JSON).
    
    Guards against the metadata payload itself exceeding MAX_PAYLOAD by
    truncating an excessively long filename — all other fields are fixed-length.
    """
    # Truncate filename if it would blow the QR size limit.
    # A safe budget: MAX_PAYLOAD minus ~120 bytes for all other JSON fields.
    MAX_FILENAME = MAX_PAYLOAD - 120
    safe_filename = filename if len(filename.encode("utf-8")) <= MAX_FILENAME else filename[:MAX_FILENAME]

    meta = {
        "magic": MAGIC.hex(),
        "filename": safe_filename,
        "total_chunks": total_chunks,
        "original_size": original_size,
        "compressed_size": compressed_size,
        "compression": compression,
        "sha256": file_hash,
        "version": "1.0",
    }
    payload = json.dumps(meta).encode("utf-8")

    # Hard safety check — should never trigger after the truncation above,
    # but guards against unexpected growth (e.g. huge total_chunks number).
    if HEADER_SIZE + len(payload) > MAX_QR_BINARY:
        raise ValueError(
            f"Metadata chunk too large ({HEADER_SIZE + len(payload)} bytes). "
            "Try shortening the filename."
        )

    crc = zlib.crc32(payload) & 0xFFFFFFFF
    header = file_uuid + struct.pack(">I", 0) + struct.pack(">I", total_chunks) + struct.pack(">I", crc)
    return header + payload

def build_data_chunk(file_uuid: bytes, index: int, total: int, data: bytes) -> bytes:
    crc = zlib.crc32(data) & 0xFFFFFFFF
    header = file_uuid + struct.pack(">I", index) + struct.pack(">I", total) + struct.pack(">I", crc)
    return header + data

def parse_chunk(raw: bytes) -> dict | None:
    if len(raw) < HEADER_SIZE:
        return None
    file_uuid = raw[:16]
    idx = struct.unpack(">I", raw[16:20])[0]
    total = struct.unpack(">I", raw[20:24])[0]
    crc_stored = struct.unpack(">I", raw[24:28])[0]
    payload = raw[28:]
    crc_calc = zlib.crc32(payload) & 0xFFFFFFFF
    return {
        "uuid": file_uuid,
        "index": idx,
        "total": total,
        "crc_ok": crc_stored == crc_calc,
        "crc_stored": crc_stored,
        "crc_calc": crc_calc,
        "payload": payload,
    }

def generate_qr_image(data: bytes, box_size: int = 10, border: int = 4,
                      dark_mode: bool = False) -> Image.Image:
    """Generate a QR code image from binary data."""
    # Guard: raise clear errors before the qrcode library raises cryptic ones.
    # len==0 -> glog(0) crash;  len>MAX -> data_cache overflow crash.
    if len(data) == 0:
        raise ValueError("Cannot generate a QR code for an empty chunk (0 bytes).")
    if len(data) > MAX_QR_BINARY:
        raise ValueError(
            f"Chunk too large: {len(data)} bytes exceeds QR v40-L max of "
            f"{MAX_QR_BINARY} bytes. Reduce the chunk size in Settings."
        )
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    fill = "white" if dark_mode else "black"
    back = "black" if dark_mode else "white"
    img = qr.make_image(fill_color=fill, back_color=back).convert("RGB")
    return img

def human_size(n: int) -> str:
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"

# ─── Page Config ─────────────────────────────────────────────────────

st.set_page_config(page_title="QR File Encoder/Decoder", page_icon="📦", layout="wide")

st.markdown("""
<style>
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] {
        padding: 10px 24px;
        border-radius: 8px 8px 0 0;
        font-weight: 600;
    }
    .stat-card {
        background: linear-gradient(135deg, #1e3a5f 0%, #0d1b2a 100%);
        color: white; padding: 20px; border-radius: 12px;
        text-align: center; margin: 4px;
    }
    .stat-card h3 { margin: 0; font-size: 28px; }
    .stat-card p { margin: 4px 0 0 0; opacity: 0.8; font-size: 14px; }
</style>
""", unsafe_allow_html=True)

# ─── Settings (session state) ────────────────────────────────────────

if "settings" not in st.session_state:
    st.session_state.settings = {
        "chunk_size": DEFAULT_CHUNK,
        "qr_box_size": 8,
        "compression_level": 3,
        "dark_qr": False,
        "qr_border": 4,
    }

settings = st.session_state.settings

# ─── Tabs ────────────────────────────────────────────────────────────

tab_gen, tab_dec, tab_settings, tab_about = st.tabs(
    ["🔐 Generate", "📷 Decode", "⚙️ Settings", "ℹ️ About"]
)

# ══════════════════════════════════════════════════════════════════════
#  GENERATE TAB
# ══════════════════════════════════════════════════════════════════════

with tab_gen:
    st.header("Encode File → QR Codes")
    st.caption("Upload any file. It will be split into scannable QR codes that can reconstruct the original file offline.")

    uploaded = st.file_uploader("Choose a file (any type, any size)", type=None, key="gen_upload")

    use_compression = st.checkbox("Enable compression (recommended for text, MP3, etc.)", value=True)

    if uploaded is not None:
        raw_data = uploaded.read()
        original_size = len(raw_data)
        filename = uploaded.name
        file_hash = hashlib.sha256(raw_data).hexdigest()

        st.info(f"📄 **{filename}** — {human_size(original_size)}")

        # Compress
        if use_compression:
            with st.spinner("Compressing..."):
                compressed, comp_method = compress_data(raw_data, settings["compression_level"])
        else:
            compressed = raw_data
            comp_method = "none"

        comp_size = len(compressed)
        ratio = (1 - comp_size / original_size) * 100 if original_size > 0 else 0

        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown(f'<div class="stat-card"><h3>{human_size(original_size)}</h3><p>Original</p></div>', unsafe_allow_html=True)
        with col2:
            st.markdown(f'<div class="stat-card"><h3>{human_size(comp_size)}</h3><p>After Compression</p></div>', unsafe_allow_html=True)
        with col3:
            saved_pct = f"{ratio:.1f}%"
            st.markdown(f'<div class="stat-card"><h3>{saved_pct}</h3><p>Space Saved</p></div>', unsafe_allow_html=True)

        # FIX: chunk_payload_size must not exceed MAX_PAYLOAD (QR capacity minus header overhead)
        chunk_payload_size = min(settings["chunk_size"], MAX_PAYLOAD)
        # FIX: filter out any empty trailing chunk that arises when compressed size is an exact
        # multiple of chunk_payload_size — an empty bytes object causes the glog(0) ValueError.
        data_chunks = [
            compressed[i:i + chunk_payload_size]
            for i in range(0, len(compressed), chunk_payload_size)
            if compressed[i:i + chunk_payload_size]
        ]
        total_chunks = len(data_chunks) + 1  # +1 for metadata chunk at index 0

        file_uuid = uuid.uuid4().bytes

        st.success(f"Will generate **{total_chunks}** QR codes (1 metadata + {len(data_chunks)} data chunks)")
        st.caption(f"Chunk payload: {chunk_payload_size} bytes | SHA-256: `{file_hash[:16]}...`")

        if st.button("🚀 Generate QR Codes", type="primary", use_container_width=True):
            all_chunks_raw = []
            qr_images = []

            # Metadata chunk (index 0)
            meta_raw = build_metadata_chunk(file_uuid, filename, total_chunks,
                                            original_size, comp_size, comp_method, file_hash)
            all_chunks_raw.append(meta_raw)

            # Data chunks (index 1..N)
            for i, chunk in enumerate(data_chunks):
                raw = build_data_chunk(file_uuid, i + 1, total_chunks, chunk)
                all_chunks_raw.append(raw)

            progress = st.progress(0, text="Generating QR codes...")
            for i, chunk_raw in enumerate(all_chunks_raw):
                try:
                    img = generate_qr_image(chunk_raw, box_size=settings["qr_box_size"],
                                            border=settings["qr_border"],
                                            dark_mode=settings["dark_qr"])
                except ValueError as e:
                    st.error(f"❌ {e}")
                    st.stop()
                qr_images.append(img)
                progress.progress((i + 1) / len(all_chunks_raw),
                                  text=f"Generated {i+1}/{len(all_chunks_raw)} QR codes")

            progress.progress(1.0, text="✅ All QR codes generated!")
            st.session_state["qr_images"] = qr_images
            st.session_state["qr_filename"] = filename

        # Display & download
        if "qr_images" in st.session_state and st.session_state.get("qr_filename") == filename:
            qr_images = st.session_state["qr_images"]

            st.subheader("Download Options")
            dl_col1, dl_col2 = st.columns(2)

            with dl_col1:
                # ZIP download
                zip_buf = io.BytesIO()
                with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for i, img in enumerate(qr_images):
                        img_buf = io.BytesIO()
                        img.save(img_buf, format="PNG")
                        zf.writestr(f"qr_{i:05d}.png", img_buf.getvalue())
                st.download_button("📦 Download ZIP", zip_buf.getvalue(),
                                   file_name=f"{filename}_qrcodes.zip",
                                   mime="application/zip", use_container_width=True)

            with dl_col2:
                # PDF download
                if HAS_REPORTLAB:
                    pdf_buf = io.BytesIO()
                    c = pdf_canvas.Canvas(pdf_buf, pagesize=A4)
                    page_w, page_h = A4
                    margin = 20 * mm
                    qr_print_size = 45 * mm
                    spacing = 5 * mm
                    cols = int((page_w - 2 * margin + spacing) / (qr_print_size + spacing))
                    rows = int((page_h - 2 * margin + spacing) / (qr_print_size + spacing + 8))
                    per_page = cols * rows

                    for i, img in enumerate(qr_images):
                        if i % per_page == 0 and i > 0:
                            c.showPage()
                        pos = i % per_page
                        col = pos % cols
                        row = pos // cols
                        x = margin + col * (qr_print_size + spacing)
                        y = page_h - margin - (row + 1) * (qr_print_size + spacing)
                        tmp_buf = io.BytesIO()
                        img.save(tmp_buf, format="PNG")
                        tmp_buf.seek(0)
                        from reportlab.lib.utils import ImageReader
                        c.drawImage(ImageReader(tmp_buf), x, y, qr_print_size, qr_print_size)
                        c.setFont("Helvetica", 6)
                        c.drawCentredString(x + qr_print_size / 2, y - 8, f"#{i}")

                    c.save()
                    st.download_button("📄 Download PDF", pdf_buf.getvalue(),
                                       file_name=f"{filename}_qrcodes.pdf",
                                       mime="application/pdf", use_container_width=True)
                else:
                    st.warning("Install `reportlab` for PDF export.")

            # Grid preview
            with st.expander(f"Preview QR Codes ({len(qr_images)} total)", expanded=False):
                preview_count = min(len(qr_images), 50)
                grid_cols = st.columns(5)
                for i in range(preview_count):
                    with grid_cols[i % 5]:
                        st.image(qr_images[i], caption=f"#{i}", width=140)
                if len(qr_images) > preview_count:
                    st.info(f"Showing first {preview_count} of {len(qr_images)} QR codes. Download ZIP/PDF for all.")

# ══════════════════════════════════════════════════════════════════════
#  DECODE TAB
# ══════════════════════════════════════════════════════════════════════

with tab_dec:
    st.header("Decode QR Codes → File")
    st.caption("Upload QR code images to reconstruct the original file.")

    if not HAS_PYZBAR:
        st.error("⚠️ `pyzbar` is not installed. Run `pip install pyzbar` and ensure `libzbar` is available on your system.")

    decode_method = st.radio("Scan method", ["📁 Upload QR images", "📷 Webcam (live)"], horizontal=True)

    if "decode_chunks" not in st.session_state:
        st.session_state.decode_chunks = {}
        st.session_state.decode_meta = None

    if st.button("🗑️ Reset decoder", use_container_width=False):
        st.session_state.decode_chunks = {}
        st.session_state.decode_meta = None
        st.rerun()

    def process_qr_image(img: Image.Image):
        """Decode QR from image, parse chunk, store in session."""
        if not HAS_PYZBAR:
            return 0
        results = pyzbar_decode(img)
        added = 0
        for r in results:
            raw = r.data
            chunk = parse_chunk(raw)
            if chunk is None:
                continue
            uid = chunk["uuid"]
            idx = chunk["index"]
            if not chunk["crc_ok"]:
                st.warning(f"CRC mismatch on chunk #{idx} — skipped")
                continue
            # Store
            uid_hex = uid.hex()
            if uid_hex not in st.session_state.decode_chunks:
                st.session_state.decode_chunks[uid_hex] = {}
            if idx not in st.session_state.decode_chunks[uid_hex]:
                st.session_state.decode_chunks[uid_hex][idx] = chunk["payload"]
                added += 1
                # Parse metadata
                if idx == 0:
                    try:
                        meta = json.loads(chunk["payload"].decode("utf-8"))
                        st.session_state.decode_meta = meta
                    except Exception:
                        pass
        return added

    if decode_method == "📁 Upload QR images":
        qr_files = st.file_uploader("Upload QR code images", type=["png", "jpg", "jpeg", "bmp", "gif"],
                                     accept_multiple_files=True, key="dec_upload")
        if qr_files:
            progress = st.progress(0, text="Scanning QR images...")
            total_added = 0
            for i, f in enumerate(qr_files):
                img = Image.open(f)
                total_added += process_qr_image(img)
                progress.progress((i + 1) / len(qr_files), text=f"Scanned {i+1}/{len(qr_files)}")
            progress.progress(1.0, text=f"✅ Done — {total_added} new chunks found")

    else:
        st.info("📷 Webcam scanning requires `opencv-python` and camera access. Use the upload method if webcam isn't available.")
        if HAS_CV2:
            if st.button("Start Webcam Scan", type="primary"):
                st.warning("Webcam scanning works best when running locally. Point camera at QR codes one at a time.")
                cap = cv2.VideoCapture(0)
                placeholder = st.empty()
                stop = st.button("Stop scanning")
                scanned_count = 0
                while cap.isOpened() and not stop:
                    ret, frame = cap.read()
                    if not ret:
                        break
                    img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    added = process_qr_image(img)
                    scanned_count += added
                    placeholder.image(img, caption=f"Scanning... ({scanned_count} chunks found)", width=500)
                cap.release()

    # Show collection status
    for uid_hex, chunks in st.session_state.decode_chunks.items():
        meta = st.session_state.decode_meta
        if meta:
            total = meta["total_chunks"]
            collected = len(chunks)
            pct = collected / total * 100
            st.progress(collected / total, text=f"Collected {collected}/{total} chunks ({pct:.1f}%)")

            st.markdown(f"""
            | Property | Value |
            |----------|-------|
            | Filename | `{meta['filename']}` |
            | Original size | {human_size(meta['original_size'])} |
            | Compressed size | {human_size(meta['compressed_size'])} |
            | Compression | {meta['compression']} |
            | SHA-256 | `{meta['sha256'][:24]}...` |
            """)

            if collected == total:
                st.success("🎉 All chunks collected! Ready to reconstruct.")
                if st.button("📥 Reconstruct & Download File", type="primary", use_container_width=True):
                    # Reassemble
                    data_parts = []
                    for i in range(1, total):
                        if i not in chunks:
                            st.error(f"Missing chunk #{i}!")
                            break
                        data_parts.append(chunks[i])
                    else:
                        compressed = b"".join(data_parts)
                        comp_method = meta["compression"]
                        if comp_method != "none":
                            try:
                                recovered = decompress_data(compressed, comp_method)
                            except Exception as e:
                                st.error(f"Decompression failed: {e}")
                                recovered = None
                        else:
                            recovered = compressed

                        if recovered is not None:
                            rec_hash = hashlib.sha256(recovered).hexdigest()
                            if rec_hash == meta["sha256"]:
                                st.success(f"✅ File integrity verified (SHA-256 match)")
                            else:
                                st.warning("⚠️ SHA-256 mismatch — file may be corrupted")

                            st.download_button("💾 Save File", recovered,
                                               file_name=meta["filename"],
                                               use_container_width=True)
            else:
                missing = [i for i in range(total) if i not in chunks]
                if len(missing) <= 20:
                    st.warning(f"Missing chunks: {missing}")
                else:
                    st.warning(f"Missing {len(missing)} chunks. Keep scanning!")
        else:
            st.info(f"Found {len(chunks)} chunks. Metadata chunk (index 0) not yet scanned.")

# ══════════════════════════════════════════════════════════════════════
#  SETTINGS TAB
# ══════════════════════════════════════════════════════════════════════

with tab_settings:
    st.header("⚙️ Settings")

    st.subheader("QR Generation")
    # FIX: hard-cap the slider max at MAX_PAYLOAD so header overhead never pushes
    # the total chunk size past the QR v40-L binary limit (2953 bytes).
    safe_default = min(settings["chunk_size"], MAX_PAYLOAD)
    settings["chunk_size"] = st.slider(
        "Payload bytes per QR chunk",
        min_value=500,
        max_value=MAX_PAYLOAD,          # 2925 — leaves room for the 28-byte header
        value=safe_default,
        step=100,
        help=(
            f"Each chunk also carries a {HEADER_SIZE}-byte header, so the hard maximum "
            f"payload is {MAX_PAYLOAD} bytes (= {MAX_QR_BINARY} QR limit − {HEADER_SIZE} header). "
            "Lower values produce more QR codes but are easier to scan in poor conditions."
        ),
    )
    settings["qr_box_size"] = st.slider("QR module size (px)", 4, 20, settings["qr_box_size"],
                                         help="Larger = bigger image, easier to scan")
    settings["qr_border"] = st.slider("QR border modules", 1, 10, settings["qr_border"])
    settings["dark_qr"] = st.checkbox("Dark mode QR codes (inverted)", value=settings["dark_qr"])

    st.subheader("Compression")
    settings["compression_level"] = st.slider("Compression level", 1, 22 if HAS_ZSTD else 9,
                                               settings["compression_level"],
                                               help="Higher = better compression, slower")
    comp_lib = "zstandard" if HAS_ZSTD else "zlib (install zstandard for better compression)"
    st.caption(f"Compression library: **{comp_lib}**")

    st.subheader("System Info")
    st.json({
        "pyzbar": HAS_PYZBAR,
        "opencv": HAS_CV2,
        "zstandard": HAS_ZSTD,
        "reportlab": HAS_REPORTLAB,
        "header_size_bytes": HEADER_SIZE,
        "max_qr_binary_v40L": MAX_QR_BINARY,
        "max_safe_payload": MAX_PAYLOAD,
    })

# ══════════════════════════════════════════════════════════════════════
#  ABOUT TAB
# ══════════════════════════════════════════════════════════════════════

with tab_about:
    st.header("ℹ️ About QR File Encoder/Decoder")
    st.markdown("""
    ### How it works

    A single QR code (Version 40, Error Correction L) can store at most **2,953 bytes** of binary data.
    To encode files larger than this, we:

    1. **Compress** the file using Zstandard (or zlib) to reduce size
    2. **Split** the compressed data into chunks of up to **2,925 bytes** each  
       *(2,953 QR max − 28-byte chunk header = 2,925 bytes safe payload)*
    3. **Wrap** each chunk with a header containing:
       - 16-byte UUID (identifies which file this chunk belongs to)
       - 4-byte chunk index
       - 4-byte total chunk count
       - 4-byte CRC32 checksum
    4. **Generate** a QR code for each chunk + one metadata QR (chunk #0)

    ### Example: 10 MB MP3 file

    | Step | Size |
    |------|------|
    | Original | 10,000,000 bytes (10 MB) |
    | After Zstd compression (~15% savings) | ~8,500,000 bytes |
    | Chunks at 2,200 bytes each | **~3,864 QR codes** + 1 metadata = **3,865 total** |

    ### Decoding

    QR images can be scanned in **any order**. The decoder uses the UUID and chunk index
    to reassemble everything correctly. CRC32 checksums verify each chunk's integrity,
    and a final SHA-256 hash confirms the entire file matches the original.

    ### 100% Offline

    This entire application runs locally. No data is uploaded anywhere.
    Your files never leave your machine.

    ---

    ### Installation

    ```bash
    pip install -r requirements.txt
    streamlit run app.py
    ```

    **System dependency**: `pyzbar` requires `libzbar0`:
    - Ubuntu/Debian: `sudo apt install libzbar0`
    - macOS: `brew install zbar`
    - Windows: included with the `pyzbar` pip package

    ### Desktop App

    To package as a standalone desktop app:
    ```bash
    pip install pyinstaller
    pyinstaller --onefile --add-data "app.py:." app.py
    ```
    Or simply run `streamlit run app.py` — it works offline with no internet required.
    """)

    st.caption("Built with ❤️ using Streamlit, qrcode, pyzbar, and zstandard")
