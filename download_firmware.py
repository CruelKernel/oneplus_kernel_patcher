#!/usr/bin/env python3
"""
OnePlus Open Firmware Downloader
Downloads the latest full firmware (not OTA) for OnePlus Open CPH2551
"""

import sys
import hashlib
import argparse
import json
import time
import threading
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode

# API Configuration
API_BASE = "https://oxygenupdater.com/api/v2.9"
DEFAULT_DEVICE_NAME = "OnePlus Open"  # Default device name
DEFAULT_VARIANT = "11"  # CPH2551_11 variant (11 typically means North America/Global)
USER_AGENT = "Oxygen_updater_6.7.6"
TIMEOUT = 30  # Connection timeout in seconds
DOWNLOAD_TIMEOUT = 300  # Download timeout per chunk (5 minutes)
CHUNK_SIZE = 5 * 1024 * 1024  # 5 MB chunks for downloads and MD5 calculation

# Regional variant mapping
VARIANT_TO_REGION = {
    "11": "NA",  # North America/Global
    "13": "EU",  # European Union
    "14": "IN",  # India
}

# Detect if running in interactive mode (TTY)
IS_INTERACTIVE = sys.stdout.isatty()

# ANSI color codes for better output (only when interactive)
class Colors:
    if IS_INTERACTIVE:
        HEADER = '\033[95m'
        OKBLUE = '\033[94m'
        OKGREEN = '\033[92m'
        WARNING = '\033[93m'
        FAIL = '\033[91m'
        ENDC = '\033[0m'
        BOLD = '\033[1m'
    else:
        # No colors in non-interactive mode
        HEADER = ''
        OKBLUE = ''
        OKGREEN = ''
        WARNING = ''
        FAIL = ''
        ENDC = ''
        BOLD = ''


def print_header(text: str):
    """Print colored header"""
    print(f"\n{Colors.HEADER}{Colors.BOLD}{text}{Colors.ENDC}")


def print_success(text: str):
    """Print success message"""
    symbol = "✓" if IS_INTERACTIVE else "[OK]"
    print(f"{Colors.OKGREEN}{symbol} {text}{Colors.ENDC}")


def print_error(text: str):
    """Print error message"""
    symbol = "✗" if IS_INTERACTIVE else "[ERROR]"
    print(f"{Colors.FAIL}{symbol} {text}{Colors.ENDC}", file=sys.stderr)


def print_info(text: str):
    """Print info message"""
    symbol = "ℹ" if IS_INTERACTIVE else "[INFO]"
    print(f"{Colors.OKBLUE}{symbol} {text}{Colors.ENDC}")


def print_warning(text: str):
    """Print warning message"""
    symbol = "⚠" if IS_INTERACTIVE else "[WARNING]"
    print(f"{Colors.WARNING}{symbol} {text}{Colors.ENDC}")


def display_progress(downloaded: int, total: int, last_reported: float) -> float:
    """Display download progress. Returns updated last_reported value."""
    if total <= 0:
        return last_reported

    percent = (downloaded / total) * 100
    downloaded_mb = downloaded / (1024**2)
    total_mb = total / (1024**2)

    if IS_INTERACTIVE:
        bar_length = 40
        filled = int(bar_length * downloaded / total)
        bar = '█' * filled + '░' * (bar_length - filled)
        print(f"\r  [{bar}] {percent:.1f}% ({downloaded_mb:.1f}/{total_mb:.1f} MB)", end='', flush=True)
        return last_reported
    else:
        if percent - last_reported >= 10:
            print(f"  Progress: {percent:.1f}% ({downloaded_mb:.1f}/{total_mb:.1f} MB)")
            return percent
        return last_reported


def make_request(url: str, params: Optional[dict[str, str]] = None):
    """Make an HTTP GET request using urllib"""
    try:
        if params:
            url = f"{url}?{urlencode(params)}"

        req = Request(url)
        req.add_header('User-Agent', USER_AGENT)

        with urlopen(req, timeout=TIMEOUT) as response:
            data = response.read().decode('utf-8')
            return json.loads(data)
    except (URLError, HTTPError, json.JSONDecodeError):
        return None


def fetch_devices() -> Optional[list[dict]]:
    """Fetch all enabled devices from the API"""
    print_header("Step 1: Fetching device list...")
    devices = make_request(f"{API_BASE}/devices/enabled")
    if devices and isinstance(devices, list):
        print_success(f"Found {len(devices)} enabled devices")
        return devices
    else:
        print_error("Failed to fetch devices")
        return None


def find_device_id(devices: list[dict], device_name: str, variant: str) -> Optional[int]:
    """Find device ID by device name and variant (regional suffix)"""
    print_header("Step 2: Looking for OnePlus Open...")

    # Map variant to region
    region = VARIANT_TO_REGION.get(variant)
    if not region:
        print_error(f"Unknown variant: {variant}")
        print_info(f"Supported variants: {', '.join(VARIANT_TO_REGION.keys())}")
        return None

    target_name = f"{device_name} ({region})"

    for device in devices:
        name = device.get('name', '')
        if name == target_name:
            device_id = device['id']
            print_success(f"Found device: {name} (ID: {device_id})")
            product_names = device.get('productNames', [])
            if product_names:
                print_info(f"Product names: {', '.join(product_names)}")
            return device_id

    print_error(f"Device '{target_name}' not found")
    print_info("Available OnePlus Open variants:")
    for device in devices:
        name = device.get('name', '')
        if device_name in name:
            print_info(f"  - {name} (ID: {device['id']})")
    return None


def fetch_update_methods(device_id: int) -> Optional[list[dict]]:
    """Fetch update methods for the device"""
    print_header("Step 3: Fetching update methods...")
    methods = make_request(f"{API_BASE}/updateMethods/{device_id}", {"language": "en"})
    if methods and isinstance(methods, list):
        print_success(f"Found {len(methods)} update methods")
        for method in methods:
            print_info(f"  - {method['name']} (ID: {method['id']})")
        return methods
    else:
        print_error("Failed to fetch update methods")
        return None


def select_full_firmware_method(methods: list[dict]) -> Optional[int]:
    """Select the full firmware update method (not OTA)"""
    # "Oxygen Updater" method is OTA (incremental)
    # "Local Upgrade" method is full firmware
    for method in methods:
        method_name = method.get('name', '').lower()
        if 'local' in method_name or 'upgrade' in method_name:
            print_success(f"Selected method: {method['name']} (full firmware)")
            return method['id']

    # Fallback: use first method
    if methods:
        print_warning(f"Using first available method: {methods[0]['name']}")
        return methods[0]['id']

    return None


def fetch_latest_firmware(device_id: int, method_id: int) -> Optional[dict]:
    """Fetch the latest firmware update data"""
    print_header("Step 4: Fetching latest firmware info...")
    firmware = make_request(f"{API_BASE}/mostRecentUpdateData/{device_id}/{method_id}")
    if firmware and isinstance(firmware, dict):
        # Display firmware information
        print_success("Firmware details:")
        print(f"  Version:      {firmware.get('version_number', 'N/A')}")
        print(f"  OTA Version:  {firmware.get('ota_version_number', 'N/A')}")
        print(f"  Filename:     {firmware.get('filename', 'N/A')}")
        print(f"  Size:         {firmware.get('download_size', 0) / (1024**3):.2f} GB")
        print(f"  MD5:          {firmware.get('md5sum', 'N/A')}")
        download_url = firmware.get('download_url', 'N/A')
        print(f"  Download URL: {download_url[:80] if download_url != 'N/A' else 'N/A'}...")
        return firmware
    else:
        print_error("Failed to fetch firmware info")
        return None


def calculate_md5(file_path: Path) -> str:
    """Calculate MD5 checksum of a file"""
    md5 = hashlib.md5()
    with open(file_path, 'rb') as f:
        while chunk := f.read(CHUNK_SIZE):
            md5.update(chunk)
    return md5.hexdigest()


def check_range_support(url: str) -> tuple[bool, int]:
    """Check if server supports byte range requests and get file size"""
    try:
        req = Request(url, method='HEAD')
        req.add_header('User-Agent', USER_AGENT)

        with urlopen(req, timeout=TIMEOUT) as response:
            accept_ranges = response.headers.get('Accept-Ranges', '')
            content_length = response.headers.get('Content-Length', '0')

            supports_range = accept_ranges.lower() == 'bytes'
            file_size = int(content_length) if content_length.isdigit() else 0

            return supports_range, file_size
    except (URLError, HTTPError):
        return False, 0


def download_chunk(url: str, start: int, end: int, output_path: Path,
                   chunk_id: int, progress_tracker: dict, lock: threading.Lock) -> bool:
    """Download a specific byte range and write directly to disk"""
    try:
        req = Request(url)
        req.add_header('User-Agent', USER_AGENT)
        req.add_header('Range', f'bytes={start}-{end}')

        with urlopen(req, timeout=DOWNLOAD_TIMEOUT) as response:
            if response.status != 206:
                return False

            downloaded = 0
            total_chunk_size = end - start + 1

            # Open file for this thread (each thread has its own file handle)
            with open(output_path, 'r+b') as f:
                # Seek to starting position
                f.seek(start)

                while downloaded < total_chunk_size:
                    to_read = min(CHUNK_SIZE, total_chunk_size - downloaded)
                    data = response.read(to_read)

                    if not data:
                        break

                    # Write to disk immediately (file handles are independent, seek isolates writes)
                    f.write(data)
                    downloaded += len(data)

                    # Update progress tracker (thread-safe)
                    with lock:
                        progress_tracker[chunk_id] = downloaded

            return downloaded == total_chunk_size

    except (URLError, HTTPError, IOError) as e:
        return False


def download_file_multiconnection(url: str, output_path: Path, total_size: int,
                                   num_connections: int) -> bool:
    """Download file using multiple parallel connections with streaming to disk"""
    print_info(f"Using {num_connections} parallel connections")

    # Pre-allocate file with correct size
    try:
        with open(output_path, 'wb') as f:
            f.seek(total_size - 1)
            f.write(b'\0')
    except IOError as e:
        print_error(f"Failed to create output file: {e}")
        return False

    # Calculate byte ranges for each connection
    chunk_size = total_size // num_connections
    ranges = []

    for i in range(num_connections):
        start = i * chunk_size
        # Last chunk gets any remaining bytes
        end = total_size - 1 if i == num_connections - 1 else (i + 1) * chunk_size - 1
        ranges.append((start, end))

    # Progress tracking
    progress_tracker = {i: 0 for i in range(num_connections)}
    lock = threading.Lock()
    results = {}

    def worker(chunk_id: int, start: int, end: int):
        """Worker thread for downloading a chunk"""
        success = download_chunk(url, start, end, output_path, chunk_id, progress_tracker, lock)
        with lock:
            results[chunk_id] = success

    # Start all download threads
    threads = []
    for i, (start, end) in enumerate(ranges):
        thread = threading.Thread(target=worker, args=(i, start, end))
        thread.start()
        threads.append(thread)

    # Progress display
    last_percent_reported = -10.0

    while any(t.is_alive() for t in threads):
        with lock:
            total_downloaded = sum(progress_tracker.values())
        last_percent_reported = display_progress(total_downloaded, total_size, last_percent_reported)
        time.sleep(0.2)

    # Wait for all threads to complete
    for thread in threads:
        thread.join()

    # Final progress update
    with lock:
        total_downloaded = sum(progress_tracker.values())
    display_progress(total_downloaded, total_size, last_percent_reported)
    if IS_INTERACTIVE:
        print()  # New line after progress bar

    # Check if all chunks downloaded successfully
    all_success = all(results.values()) if results else False

    if all_success and total_downloaded == total_size:
        return True
    else:
        print_error(f"Download incomplete: {total_downloaded}/{total_size} bytes")
        return False


def download_file(url: str, filename: str, expected_md5: Optional[str] = None, no_clobber: bool = False, num_connections: int = 1) -> bool:
    """Download file with progress bar and optional MD5 verification"""
    print_header("Step 5: Downloading firmware...")

    output_path = Path(filename)

    # Check if file already exists
    if output_path.exists():
        print_info(f"File already exists: {output_path}")

        # If no-clobber is set, skip download
        if no_clobber:
            print_success("Skipping download (--no-clobber flag set)")
            return True

        if expected_md5:
            print_info("Verifying existing file...")
            actual_md5 = calculate_md5(output_path)
            if actual_md5.lower() == expected_md5.lower():
                print_success("Existing file MD5 matches! Download not needed.")
                return True
            else:
                print_warning("Existing file MD5 mismatch, re-downloading...")
                output_path.unlink()
        else:
            user_input = input("Re-download? [y/N]: ")
            if user_input.lower() != 'y':
                return True
            output_path.unlink()

    # Check if multi-connection download is requested and supported
    use_multiconnection = False
    total_size = 0

    if num_connections > 1:
        print_info(f"Checking server support for multi-connection downloads...")
        supports_range, file_size = check_range_support(url)
        total_size = file_size

        if supports_range and file_size > 0:
            print_success(f"Server supports range requests (file size: {file_size / (1024**3):.2f} GB)")
            use_multiconnection = True
        else:
            print_info("Server does not support range requests, falling back to single connection")
            num_connections = 1

    try:
        if use_multiconnection:
            # Multi-connection download
            success = download_file_multiconnection(url, output_path, total_size, num_connections)
            if not success:
                print_error("Multi-connection download failed")
                return False
            print_success(f"Download complete: {output_path}")
        else:
            # Single connection download
            req = Request(url)
            req.add_header('User-Agent', USER_AGENT)

            with urlopen(req, timeout=DOWNLOAD_TIMEOUT) as response:
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                last_percent_reported = -10.0

                with open(output_path, 'wb') as f:
                    while True:
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        last_percent_reported = display_progress(downloaded, total_size, last_percent_reported)

            if IS_INTERACTIVE:
                print()  # New line after progress bar
            print_success(f"Download complete: {output_path}")

        # Verify MD5 if provided
        if expected_md5:
            print_header("Step 6: Verifying MD5 checksum...")
            print_info("Calculating MD5 (this may take a minute)...")
            actual_md5 = calculate_md5(output_path)

            if actual_md5.lower() == expected_md5.lower():
                print_success(f"MD5 verification passed: {actual_md5}")
                return True
            else:
                print_error(f"MD5 mismatch!")
                print_error(f"  Expected: {expected_md5}")
                print_error(f"  Actual:   {actual_md5}")
                print_error(f"File may be corrupted, please re-download")
                return False

        return True

    except (URLError, HTTPError) as e:
        print_error(f"Download failed: {e}")
        if output_path.exists():
            output_path.unlink()
        return False


def get_firmware_info(device_name: str, variant: str) -> Optional[dict]:
    """Get firmware info without downloading. Returns firmware dict or None on error."""
    # Suppress output during info gathering
    import io
    import contextlib

    # Step 1: Fetch all devices
    devices = make_request(f"{API_BASE}/devices/enabled")
    if not devices or not isinstance(devices, list):
        return None

    # Step 2: Find device ID
    region = VARIANT_TO_REGION.get(variant)
    if not region:
        return None
    target_name = f"{device_name} ({region})"
    device_id = None
    for device in devices:
        if device.get('name', '') == target_name:
            device_id = device['id']
            break
    if not device_id:
        return None

    # Step 3: Fetch update methods
    methods = make_request(f"{API_BASE}/updateMethods/{device_id}", {"language": "en"})
    if not methods or not isinstance(methods, list):
        return None

    # Step 4: Select full firmware method
    method_id = None
    for method in methods:
        method_name = method.get('name', '').lower()
        if 'local' in method_name or 'upgrade' in method_name:
            method_id = method['id']
            break
    if not method_id and methods:
        method_id = methods[0]['id']
    if not method_id:
        return None

    # Step 5: Fetch latest firmware
    firmware = make_request(f"{API_BASE}/mostRecentUpdateData/{device_id}/{method_id}")
    if not firmware or not isinstance(firmware, dict):
        return None

    return firmware


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description='OnePlus Firmware Downloader - Downloads full firmware (not OTA) from OxygenOS Updater',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  %(prog)s                              # Download OnePlus Open (NA) firmware
  %(prog)s --device "OnePlus Open"      # Specify device name
  %(prog)s --variant 11                 # Specify variant (NA/Global)
  %(prog)s --variant 13                 # Download EU variant
  %(prog)s --variant 14                 # Download India variant
  %(prog)s --no-clobber                 # Skip download if file exists
  %(prog)s -c                           # Short form of --no-clobber
  %(prog)s --num-connections 4          # Use 4 parallel connections
  %(prog)s -n 8                         # Use 8 parallel connections (faster)
  %(prog)s --check-only --json          # Output firmware info as JSON (for CI)
  %(prog)s --output-dir ./downloads     # Download to specific directory

Supported variants:
  11 = NA (North America/Global)
  13 = EU (European Union)
  14 = IN (India)
        """
    )
    parser.add_argument(
        '--device',
        type=str,
        default=DEFAULT_DEVICE_NAME,
        help=f'Device name (default: "{DEFAULT_DEVICE_NAME}")'
    )
    parser.add_argument(
        '--variant',
        type=str,
        default=DEFAULT_VARIANT,
        choices=['11', '13', '14'],
        help=f'Device variant/region (default: {DEFAULT_VARIANT})'
    )
    parser.add_argument(
        '--no-clobber', '-c',
        action='store_true',
        help='Skip download if a file with the same name already exists'
    )
    parser.add_argument(
        '--num-connections', '-n',
        type=int,
        default=1,
        choices=range(1, 17),
        metavar='N',
        help='Number of parallel connections (1-16, default: 1)'
    )
    parser.add_argument(
        '--check-only',
        action='store_true',
        help='Check for updates without downloading (outputs firmware info)'
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='Output firmware info as JSON (useful for CI pipelines)'
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('.'),
        help='Output directory for downloaded firmware (default: current directory)'
    )
    return parser.parse_args()


def main():
    """Main function"""
    args = parse_args()

    # Handle --check-only mode (for CI pipelines)
    if args.check_only:
        firmware = get_firmware_info(args.device, args.variant)
        if not firmware:
            if args.json:
                print('{"error": "Failed to fetch firmware info"}')
            else:
                print_error("Failed to fetch firmware info")
            sys.exit(1)

        if args.json:
            # Output as JSON for easy parsing in CI
            output = {
                'version_number': firmware.get('version_number'),
                'ota_version_number': firmware.get('ota_version_number'),
                'filename': firmware.get('filename'),
                'download_size': firmware.get('download_size'),
                'md5sum': firmware.get('md5sum'),
                'download_url': firmware.get('download_url'),
                'device': args.device,
                'variant': args.variant,
                'region': VARIANT_TO_REGION.get(args.variant, 'Unknown'),
            }
            print(json.dumps(output, indent=2))
        else:
            # Human-readable output
            print(f"Version:      {firmware.get('version_number', 'N/A')}")
            print(f"OTA Version:  {firmware.get('ota_version_number', 'N/A')}")
            print(f"Filename:     {firmware.get('filename', 'N/A')}")
            print(f"Size:         {firmware.get('download_size', 0) / (1024**3):.2f} GB")
            print(f"MD5:          {firmware.get('md5sum', 'N/A')}")
        sys.exit(0)

    # Regular download mode
    print(f"\n{Colors.BOLD}{'='*70}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.OKGREEN}OnePlus Firmware Downloader{Colors.ENDC}")
    print(f"{Colors.BOLD}{'='*70}{Colors.ENDC}")
    print(f"Device: {args.device}")
    print(f"Variant: {args.variant} ({VARIANT_TO_REGION.get(args.variant, 'Unknown')})")

    # Step 1: Fetch all devices
    devices = fetch_devices()
    if not devices:
        sys.exit(1)

    # Step 2: Find device ID
    device_id = find_device_id(devices, args.device, args.variant)
    if not device_id:
        sys.exit(1)

    # Step 3: Fetch update methods
    methods = fetch_update_methods(device_id)
    if not methods:
        sys.exit(1)

    # Select full firmware method
    method_id = select_full_firmware_method(methods)
    if not method_id:
        print_error("No update method available")
        sys.exit(1)

    # Step 4: Fetch latest firmware
    firmware = fetch_latest_firmware(device_id, method_id)
    if not firmware:
        sys.exit(1)

    download_url = firmware.get('download_url')
    filename = firmware.get('filename')
    md5sum = firmware.get('md5sum')

    if not download_url or not filename:
        print_error("Missing download URL or filename in API response")
        sys.exit(1)

    # Handle output directory
    if args.output_dir != Path('.'):
        args.output_dir.mkdir(parents=True, exist_ok=True)
        filename = str(args.output_dir / filename)

    # Step 5-6: Download and verify
    success = download_file(download_url, filename, md5sum, args.no_clobber, args.num_connections)

    if success:
        done_symbol = "✓" if IS_INTERACTIVE else ""
        print_header(f"{done_symbol} All done!".strip())
        print_success(f"Firmware saved to: {Path(filename).absolute()}")
        print_info(f"You can now flash this firmware using the OnePlus Local Upgrade method")
    else:
        print_error("Download or verification failed")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Colors.WARNING}Download cancelled by user{Colors.ENDC}")
        sys.exit(130)
