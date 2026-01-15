#!/usr/bin/env python3
"""
Android payload.bin extractor
Extracts partition images from OTA payload.bin files (Brillo/AOSP format)
No external dependencies - uses only Python standard library
"""

import sys
import struct
import lzma
import bz2
import hashlib
import argparse
from pathlib import Path
from dataclasses import dataclass, field

PAYLOAD_MAGIC = b'CrAU'
BLOCK_SIZE = 4096

OP_REPLACE = 0
OP_REPLACE_BZ = 1
OP_SOURCE_COPY = 4
OP_SOURCE_BSDIFF = 5
OP_ZERO = 6
OP_REPLACE_XZ = 8
OP_PUFFDIFF = 9

OP_NAMES = {
    0: 'REPLACE', 1: 'REPLACE_BZ', 4: 'SOURCE_COPY', 5: 'SOURCE_BSDIFF',
    6: 'ZERO', 8: 'REPLACE_XZ', 9: 'PUFFDIFF', 14: 'ZSTD', 15: 'LZ4'
}


@dataclass
class Operation:
    op_type: int = 0
    data_offset: int = 0
    data_length: int = 0
    dst_extents: list = field(default_factory=list)  # list of (start_block, num_blocks)
    data_sha256: bytes = b''


@dataclass
class Partition:
    name: str = ''
    operations: list = field(default_factory=list)
    size: int = 0


@dataclass
class Payload:
    path: Path = None
    data_offset: int = 0
    block_size: int = BLOCK_SIZE
    partitions: list = field(default_factory=list)


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Read varint, return (value, new_position)"""
    result = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, pos
        shift += 7
    raise ValueError("Truncated varint")


def iter_fields(data: bytes):
    """Iterate protobuf fields, yielding (field_number, value)"""
    pos = 0
    while pos < len(data):
        tag, pos = read_varint(data, pos)
        field_num = tag >> 3
        wire_type = tag & 7

        if wire_type == 0:  # varint
            value, pos = read_varint(data, pos)
        elif wire_type == 2:  # length-delimited
            length, pos = read_varint(data, pos)
            value = data[pos:pos + length]
            pos += length
        elif wire_type == 1:  # 64-bit
            value = struct.unpack('<Q', data[pos:pos + 8])[0]
            pos += 8
        elif wire_type == 5:  # 32-bit
            value = struct.unpack('<I', data[pos:pos + 4])[0]
            pos += 4
        else:
            raise ValueError(f"Unknown wire type: {wire_type}")

        yield field_num, value


def parse_operation(data: bytes) -> Operation:
    """Parse InstallOperation message"""
    op = Operation()
    for field_num, value in iter_fields(data):
        if field_num == 1:
            op.op_type = value
        elif field_num == 2:
            op.data_offset = value
        elif field_num == 3:
            op.data_length = value
        elif field_num == 6:  # dst_extent
            start = num = 0
            for f, v in iter_fields(value):
                if f == 1:
                    start = v
                elif f == 2:
                    num = v
            op.dst_extents.append((start, num))
        elif field_num == 8:
            op.data_sha256 = value
    return op


def parse_partition(data: bytes) -> Partition:
    """Parse PartitionUpdate message"""
    part = Partition()
    for field_num, value in iter_fields(data):
        if field_num == 1:
            part.name = value.decode()
        elif field_num == 7:  # new_partition_info
            for f, v in iter_fields(value):
                if f == 1:
                    part.size = v
        elif field_num == 8:  # operation
            part.operations.append(parse_operation(value))
    return part


def load_payload(path: Path) -> Payload:
    """Load and parse payload.bin, return Payload object"""
    with open(path, 'rb') as f:
        magic = f.read(4)
        if magic != PAYLOAD_MAGIC:
            raise ValueError(f"Invalid magic: {magic!r}")

        version = struct.unpack('>Q', f.read(8))[0]
        if version != 2:
            raise ValueError(f"Unsupported version: {version}")

        manifest_size = struct.unpack('>Q', f.read(8))[0]
        signature_size = struct.unpack('>I', f.read(4))[0]
        manifest_data = f.read(manifest_size)

    payload = Payload(path=path, data_offset=24 + manifest_size + signature_size)

    for field_num, value in iter_fields(manifest_data):
        if field_num == 3:
            payload.block_size = value
        elif field_num == 13:
            payload.partitions.append(parse_partition(value))

    return payload


def decompress(data: bytes, op_type: int) -> bytes:
    """Decompress data based on operation type"""
    if op_type == OP_REPLACE:
        return data
    if op_type == OP_REPLACE_XZ:
        return lzma.decompress(data)
    if op_type == OP_REPLACE_BZ:
        return bz2.decompress(data)
    raise ValueError(f"Unsupported: {OP_NAMES.get(op_type, op_type)}")


def extract_partition(payload: Payload, partition: Partition, output_path: Path) -> bool:
    """Extract a single partition"""
    total = len(partition.operations)
    bs = payload.block_size

    with open(payload.path, 'rb') as f_in, open(output_path, 'wb') as f_out:
        for i, op in enumerate(partition.operations):
            print(f"\r  Extracting: {(i + 1) * 100 // total}% ({i + 1}/{total})", end='', flush=True)

            if op.op_type == OP_ZERO:
                for start, num in op.dst_extents:
                    f_out.seek(start * bs)
                    f_out.write(bytes(num * bs))
                continue

            if op.op_type in (OP_SOURCE_COPY, OP_SOURCE_BSDIFF, OP_PUFFDIFF):
                raise ValueError(f"Incremental op not supported: {OP_NAMES.get(op.op_type)}")

            f_in.seek(payload.data_offset + op.data_offset)
            compressed = f_in.read(op.data_length)

            if op.data_sha256 and hashlib.sha256(compressed).digest() != op.data_sha256:
                print(f"\n  Error: Hash mismatch at operation {i}")
                return False

            try:
                data = decompress(compressed, op.op_type)
            except Exception as e:
                print(f"\n  Error: {e}")
                return False

            pos = 0
            for start, num in op.dst_extents:
                size = num * bs
                f_out.seek(start * bs)
                f_out.write(data[pos:pos + size])
                pos += size

    print()
    return True


def format_size(size: int) -> str:
    """Format byte size for display"""
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f"{size:.2f} {unit}" if unit != 'B' else f"{size} B"
        size /= 1024


def cmd_list(payload: Payload):
    """List partitions"""
    print(f"Payload: {payload.path}")
    print(f"Block size: {payload.block_size}")
    print(f"Partitions: {len(payload.partitions)}\n")

    print(f"{'Name':<24} {'Size':>12} {'Ops':>6}")
    print("-" * 44)
    for p in payload.partitions:
        print(f"{p.name:<24} {format_size(p.size):>12} {len(p.operations):>6}")


def cmd_extract(payload: Payload, names: list[str], output_dir: Path) -> bool:
    """Extract partitions"""
    by_name = {p.name: p for p in payload.partitions}

    for name in names:
        if name not in by_name:
            print(f"Error: '{name}' not found. Available: {', '.join(sorted(by_name))}")
            return False

    output_dir.mkdir(parents=True, exist_ok=True)

    for name in names:
        part = by_name[name]
        out = output_dir / f"{name}.img"
        print(f"\nExtracting '{name}' ({format_size(part.size)}) -> {out}")

        if not extract_partition(payload, part, out):
            return False
        print(f"  Done: {format_size(out.stat().st_size)}")

    print("\nAll done.")
    return True


def main():
    ap = argparse.ArgumentParser(
        description='Extract partitions from Android payload.bin',
        epilog="Examples:\n"
               "  %(prog)s payload.bin -l\n"
               "  %(prog)s payload.bin -p boot init_boot\n"
               "  %(prog)s payload.bin -p boot -o ./out\n",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument('payload', type=Path)
    ap.add_argument('-l', '--list', action='store_true', help='List partitions')
    ap.add_argument('-p', '--partitions', nargs='+', metavar='NAME', help='Extract partition(s)')
    ap.add_argument('-o', '--output', type=Path, default=Path('.'), help='Output directory')
    args = ap.parse_args()

    if not args.payload.exists():
        sys.exit(f"Error: {args.payload} not found")

    try:
        payload = load_payload(args.payload)

        if args.partitions:
            success = cmd_extract(payload, args.partitions, args.output)
            sys.exit(0 if success else 1)
        else:
            cmd_list(payload)
    except Exception as e:
        sys.exit(f"Error: {e}")


if __name__ == '__main__':
    main()
