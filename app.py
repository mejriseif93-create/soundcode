import streamlit as st
import numpy as np
from PIL import Image, ImageDraw
import zstandard as zstd
import base64
import hashlib
import struct
import io
import math

Image.MAX_IMAGE_PIXELS = None  # Disable decompression bomb limits for large SoundCodes


# --- Constants & Configuration ---
MAGIC = b"SNDCODE1"
HEADER_STRUCT_BASE = ">8sIIB32sH" # Magic, OrigSize, CompSize, CompID, SHA256, FNameLen

st.set_page_config(page_title="SoundCode Offline", layout="wide")

# --- Core Logic: Encoding ---

def encode_file(file_bytes, filename, cell_size, comp_level):
    # 1. Compression
    cctx = zstd.ZstdCompressor(level=comp_level)
    compressed_data = cctx.compress(file_bytes)
    
    orig_size = len(file_bytes)
    comp_size = len(compressed_data)
    sha256_hash = hashlib.sha256(file_bytes).digest()
    fname_bytes = filename.encode('utf-8')
    fname_len = len(fname_bytes)
    
    # 2. Build Binary Payload
    header = struct.pack(HEADER_STRUCT_BASE, MAGIC, orig_size, comp_size, 2, sha256_hash, fname_len)
    full_payload = header + fname_bytes + compressed_data
    
    # 3. Base64 to Bits
    b64_payload = base64.b64encode(full_payload)
    # Convert every char to 7-bit (or 8-bit for safety with b64)
    # Using 8-bit ensures we don't lose alignment
    bit_string = "".join(f"{byte:08b}" for byte in b64_payload)
    total_bits = len(bit_string)
    
    # 4. Grid Calculations
    # We need space for: Data + 1px timing row/col + 3px border
    data_grid_size = math.ceil(math.sqrt(total_bits))
    padded_bits = bit_string.ljust(data_grid_size**2, '0')
    
    # Total Canvas = Data + Timing(1) + Border(3*2)
    canvas_size = data_grid_size + 1 + 6
    img_dim = canvas_size * cell_size
    
    # Create Image (L mode = Grayscale)
    img = Image.new('L', (img_dim, img_dim), 255) # White background
    draw = ImageDraw.Draw(img)
    
    # 5. Drawing Logic
    # 3px Border (Physical pixels)
    border_px = 3 * cell_size
    draw.rectangle([0, 0, img_dim-1, img_dim-1], outline=0, width=border_px)
    
    # Timing Row/Col (Starting at offset 3)
    for i in range(data_grid_size + 1):
        color = 0 if i % 2 == 0 else 255
        # Top timing row
        draw.rectangle([(3+i)*cell_size, 3*cell_size, (4+i)*cell_size-1, 4*cell_size-1], fill=color)
        # Left timing column
        draw.rectangle([3*cell_size, (3+i)*cell_size, 4*cell_size-1, (4+i)*cell_size-1], fill=color)
        
    # 6. Data Mapping
    bit_idx = 0
    for r in range(data_grid_size):
        for c in range(data_grid_size):
            if bit_idx < len(padded_bits):
                if padded_bits[bit_idx] == '1':
                    # Offset by 4 (3 border + 1 timing)
                    x1, y1 = (4 + c) * cell_size, (4 + r) * cell_size
                    x2, y2 = x1 + cell_size - 1, y1 + cell_size - 1
                    draw.rectangle([x1, y1, x2, y2], fill=0)
                bit_idx += 1
                
    return img, data_grid_size, orig_size, comp_size

# --- Core Logic: Decoding ---

def decode_image(img):
    img = img.convert('L')
    arr = np.array(img)
    total_px = arr.shape[0]
    
    # 1. Detect cell size via border
    # Find first white pixel after the black border to calculate border width
    border_width = 0
    while arr[border_width, border_width] < 128:
        border_width += 1
    
    cell_size = border_width // 3
    
    # 2. Timing row detection to get grid size
    # Move inward by border + half a cell to hit the timing row
    timing_start = 3 * cell_size
    grid_count = 0
    # Check along the top timing row
    for x in range(timing_start, total_px - timing_start, cell_size):
        grid_count += 1
    
    data_grid_size = grid_count - 1
    
    # 3. Read Bits
    bits = []
    data_start = 4 * cell_size
    for r in range(data_grid_size):
        for c in range(data_grid_size):
            y = data_start + (r * cell_size)
            x = data_start + (c * cell_size)
            # Sample center of cell
            sample = arr[y + cell_size//2, x + cell_size//2]
            bits.append('1' if sample < 128 else '0')
            
    bit_string = "".join(bits)
    
    # 4. Reconstruct Payload
    byte_list = [int(bit_string[i:i+8], 2) for i in range(0, len(bit_string), 8)]
    b64_data = bytes(byte_list)
    
    # Clean padding and decode base64
    try:
        full_payload = base64.b64decode(b64_data.split(b'\x00')[0]) # Simple split might be risky, but b64 is specific
    except:
        # If padding causes issues, try stripping until valid
        full_payload = base64.b64decode(b64_data[:(len(b64_data)//4)*4])

    # 5. Parse Header
    magic, o_size, c_size, c_id, sha, fn_len = struct.unpack(HEADER_STRUCT_BASE, full_payload[:51])
    if magic != MAGIC:
        raise ValueError("Not a valid SoundCode image.")
        
    fname = full_payload[51 : 51+fn_len].decode('utf-8')
    compressed_data = full_payload[51+fn_len : 51+fn_len+c_size]
    
    # 6. Decompress & Verify
    dctx = zstd.ZstdDecompressor()
    decompressed = dctx.decompress(compressed_data, max_output_size=o_size)
    
    if hashlib.sha256(decompressed).digest() != sha:
        st.error("SHA-256 verification failed! Data might be corrupted.")
        
    return decompressed, fname

# --- UI Layout ---

import streamlit.components.v1 as components

st.title("🔊 SoundCode")
st.caption("Store any file inside a custom high-density 2D visual barcode.")

tab1, tab2, tab3, tab4 = st.tabs(["Encode", "Decode", "Settings", "Mobile Web App"])

with tab3:
    c_size = st.slider("Cell Size (px)", 2, 20, 4, help="Smaller = more dense, Larger = easier to scan/print")
    c_level = st.slider("Compression Level (Zstd)", 1, 22, 3)

with tab1:
    uploaded_file = st.file_uploader("Choose a file (Audio, Doc, etc.)")
    if uploaded_file:
        file_bytes = uploaded_file.read()
        if st.button("Generate SoundCode"):
            with st.spinner("Encoding and generating grid..."):
                img, grid_dim, o_sz, c_sz = encode_file(file_bytes, uploaded_file.name, c_size, c_level)
                
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("Grid Dimensions", f"{grid_dim}x{grid_dim}")
                    st.metric("Original Size", f"{o_sz/1024:.2f} KB")
                with col2:
                    st.metric("Compressed Size", f"{c_sz/1024:.2f} KB")
                    st.metric("Image Resolution", f"{img.width}x{img.height}")
                
                # Low-res preview
                preview = img.copy()
                preview.thumbnail((800, 800))
                st.image(preview, caption="Low-res Preview (Download for full resolution)")
                
                # Download
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                st.download_button("Download SoundCode PNG", buf.getvalue(), f"{uploaded_file.name}.sc.png", "image/png")

with tab2:
    uploaded_code = st.file_uploader("Upload a SoundCode PNG", type=["png"])
    if uploaded_code:
        if st.button("Decode SoundCode"):
            try:
                sc_img = Image.open(uploaded_code)
                dec_bytes, dec_name = decode_image(sc_img)
                st.success(f"Successfully decoded: {dec_name}")
                st.download_button(f"Download {dec_name}", dec_bytes, dec_name)
            except Exception as e:
                st.error(f"Decoding failed: {e}")

with tab4:
    st.markdown("### Mobile Offline Web Version")
    st.caption("This advanced version handles chunks, QR grouping, and camera scanning directly in your browser without any server connection.")
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            html_code = f.read()
        components.html(html_code, height=800, scrolling=True)
    except FileNotFoundError:
        st.error("index.html not found!")

