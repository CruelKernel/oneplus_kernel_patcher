# OnePlus Open Kernel Patcher

Automated Magisk patching for OnePlus Open firmware. This repository automatically checks for new firmware releases daily, downloads them, extracts the boot image, patches it with Magisk, and publishes the patched images as GitHub releases.

## Quick Start

Download the latest patched boot image from the [Releases](../../releases) page and flash it:

```bash
# Boot into fastboot
adb reboot bootloader

# Flash the patched image
fastboot flash init_boot magisk_patched_init_boot.img

# Reboot
fastboot reboot
```

Then install the [Magisk app](https://github.com/topjohnwu/Magisk/releases) on your device.

## How It Works

A GitHub Actions workflow runs daily to:

1. Check for new OnePlus Open (India) firmware via the OxygenOS Updater API
2. Download the firmware if a new version is available
3. Extract `payload.bin` from the firmware ZIP
4. Extract `init_boot.img` from the payload
5. Patch the boot image with the latest Magisk
6. Create a GitHub release with both stock and patched images

## Manual Usage

### Check for firmware updates

```bash
# Human-readable output
python download_firmware.py --check-only

# JSON output (for scripts)
python download_firmware.py --check-only --json
```

### Download firmware

```bash
# Download with 8 parallel connections
python download_firmware.py -n 8

# Skip if already downloaded
python download_firmware.py --no-clobber
```

### Extract boot image

```bash
# List available partitions
python extract_payload.py payload.bin -l

# Extract init_boot
python extract_payload.py payload.bin -p init_boot -o .
```

### Patch boot image

```bash
# Download Magisk APK first
wget https://github.com/topjohnwu/Magisk/releases/latest/download/app-debug.apk -O magisk.apk

# Patch the boot image
./patch_boot.sh init_boot.img magisk.apk
```

## Restoring Stock

If you need to restore the stock boot image:

```bash
fastboot flash init_boot init_boot.img
```

## Supported Variants

- OnePlus Open (India) - variant 14 (default)

To check other variants manually:
```bash
python download_firmware.py --variant 11  # NA/Global
python download_firmware.py --variant 13  # EU
```

## License

GPL
