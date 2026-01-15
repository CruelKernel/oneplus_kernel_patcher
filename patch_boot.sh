#!/bin/bash
#######################################################################################
# Magisk Boot Image Patcher for CI
#######################################################################################
#
# Usage: patch_boot.sh <boot_image> <magisk_apk>
#
# This script sets up the Magisk patching environment for GitHub Actions CI.
# It extracts the necessary binaries from the Magisk APK and patches the boot image.
#
# IMPORTANT: This uses x86_64 magiskboot for CI execution, but ARM64 binaries
# (magiskinit, magisk, init-ld) for embedding into the boot image since the
# target device (OnePlus Open) is ARM64.
#
#######################################################################################

set -e

# Check arguments
if [ $# -lt 2 ]; then
    echo "Usage: $0 <boot_image> <magisk_apk>"
    echo ""
    echo "Arguments:"
    echo "  boot_image   Path to boot.img or init_boot.img to patch"
    echo "  magisk_apk   Path to Magisk APK (app-debug.apk)"
    exit 1
fi

BOOT_IMG="$(realpath "$1")"
MAGISK_APK="$(realpath "$2")"

# Validate inputs
if [ ! -f "$BOOT_IMG" ]; then
    echo "Error: Boot image not found: $BOOT_IMG"
    exit 1
fi

if [ ! -f "$MAGISK_APK" ]; then
    echo "Error: Magisk APK not found: $MAGISK_APK"
    exit 1
fi

# Get the original directory and boot image name
ORIGINAL_DIR="$(pwd)"
BOOT_IMG_NAME="$(basename "$BOOT_IMG" .img)"

# Create temporary working directory
WORKDIR=$(mktemp -d)
echo "Working directory: $WORKDIR"

cleanup() {
    echo "Cleaning up..."
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

# Extract Magisk APK
echo "Extracting Magisk APK..."
unzip -q "$MAGISK_APK" -d "$WORKDIR/magisk"

# Set up patching directory
PATCHDIR="$WORKDIR/patch"
mkdir -p "$PATCHDIR"
cd "$PATCHDIR"

# Copy x86_64 magiskboot binary (for running on CI)
echo "Setting up x86_64 magiskboot for CI execution..."
cp "$WORKDIR/magisk/lib/x86_64/libmagiskboot.so" ./magiskboot
chmod +x ./magiskboot

# Copy ARM64 binaries (these get embedded into the boot image for the target device)
echo "Setting up ARM64 binaries for target device..."
cp "$WORKDIR/magisk/lib/arm64-v8a/libmagiskinit.so" ./magiskinit
cp "$WORKDIR/magisk/lib/arm64-v8a/libmagisk.so" ./magisk
cp "$WORKDIR/magisk/lib/arm64-v8a/libinit-ld.so" ./init-ld

# Copy scripts and assets
echo "Copying Magisk scripts and assets..."
cp "$WORKDIR/magisk/assets/boot_patch.sh" ./
cp "$WORKDIR/magisk/assets/util_functions.sh" ./
cp "$WORKDIR/magisk/assets/stub.apk" ./

# Copy chromeos folder if exists (for Pixel C devices)
if [ -d "$WORKDIR/magisk/assets/chromeos" ]; then
    cp -r "$WORKDIR/magisk/assets/chromeos" ./
fi

# Make everything executable
chmod +x ./boot_patch.sh ./magiskinit ./magisk ./init-ld

# Set environment variables for patching
# BOOTMODE=false avoids calls to ARM64 magisk binary that can't run on x86_64 CI
# KEEPVERITY and KEEPFORCEENCRYPT preserve dm-verity and encryption
export BOOTMODE=false
export KEEPVERITY=true
export KEEPFORCEENCRYPT=true
export PATCHVBMETAFLAG=false
export RECOVERYMODE=false
export LEGACYSAR=false

# Create a minimal ui_print function for the script
# The original boot_patch.sh expects ui_print to be available
# We override util_functions.sh's ui_print to just echo
cat > ./ui_print_override.sh << 'EOF'
ui_print() {
    echo "$1"
}
abort() {
    echo "Error: $1"
    exit 1
}
# Override api_level_arch_detect to return ARM64 values
# This is important so the patching embeds ARM64 binaries
api_level_arch_detect() {
    ARCH=arm64
    ABI=arm64-v8a
    ABI32=armeabi-v7a
    IS64BIT=true
    API=34
}
# Override grep_prop and grep_get_prop to avoid device-specific calls
grep_prop() {
    echo ""
}
grep_get_prop() {
    echo ""
}
EOF

# Create a wrapper script that sets up the environment properly
cat > ./run_patch.sh << 'WRAPPER'
#!/bin/bash
set -e

# Source our overrides first
. ./ui_print_override.sh

# Set required variables
# BOOTMODE=false to avoid executing ARM64 magisk binary on x86_64 CI
BOOTMODE=false
KEEPVERITY=true
KEEPFORCEENCRYPT=true
PATCHVBMETAFLAG=false
RECOVERYMODE=false
LEGACYSAR=false
SOURCEDMODE=true
TMPDIR="$(pwd)"
OUTFD=1

# CRITICAL: Set PREINITDEVICE for OnePlus Open (CPH2551)
# Since BOOTMODE=false, boot_patch.sh cannot run `./magisk --preinit-device` to detect this.
# OnePlus Open uses the "metadata" partition for preinit storage.
# Without this, Magisk will show "Requires additional setup" after boot.
PREINITDEVICE=metadata

# Export for boot_patch.sh
export BOOTMODE KEEPVERITY KEEPFORCEENCRYPT PATCHVBMETAFLAG RECOVERYMODE LEGACYSAR OUTFD PREINITDEVICE

# Source util_functions but with our overrides already set
. ./util_functions.sh

# Override the functions again after sourcing
. ./ui_print_override.sh

# Now source and run boot_patch.sh
cd "$(dirname "$0")"
. ./boot_patch.sh "$1"
WRAPPER
chmod +x ./run_patch.sh

# Copy boot image to working directory
cp "$BOOT_IMG" ./boot_to_patch.img

echo ""
echo "=========================================="
echo "Starting Magisk boot image patching..."
echo "=========================================="
echo ""

# Run the patching
./run_patch.sh ./boot_to_patch.img

# Check for output
if [ -f "./new-boot.img" ]; then
    OUTPUT_NAME="magisk_patched_${BOOT_IMG_NAME}.img"
    cp ./new-boot.img "$ORIGINAL_DIR/$OUTPUT_NAME"
    echo ""
    echo "=========================================="
    echo "Patching complete!"
    echo "Output: $ORIGINAL_DIR/$OUTPUT_NAME"
    echo "=========================================="
else
    echo "Error: Patching failed - no output file generated"
    exit 1
fi
