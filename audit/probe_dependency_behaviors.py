"""Small, non-destructive probes of installed vulnerable dependency paths.

No application DB, server, external network, or large/exhaustion payloads used.
Windows path resolution is intercepted before any filesystem/network lookup.
"""
import asyncio
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from python_multipart.multipart import MultipartParser, parse_options_header

ROOT = Path(__file__).resolve().parents[1]
rows = {}


async def main():
    # A tiny form demonstrates that max_fields is not enforced for urlencoded.
    body = b"a=1&b=2&c=3&d=4&e=5"
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"content-type", b"application/x-www-form-urlencoded")]}, receive)
    form = await request.form(max_fields=1)
    rows["urlencoded_max_fields"] = {"requested_max_fields": 1, "parsed_fields": len(form)}

    # Benign-size crafted Range values; cap each test at 8 KB header input.
    timings = []
    for size in (1000, 2000, 4000, 8000):
        start = time.perf_counter()
        try:
            FileResponse._parse_range_header("bytes=" + "0" * size + "a-", 100000)
        except Exception:
            pass
        timings.append({"input_bytes": size + 8, "seconds": round(time.perf_counter() - start, 6)})
    rows["range_parser_bounded_timing"] = timings

    # Large relative to header limits, but only 32 KB total memory here.
    header_bytes = []
    parser = MultipartParser(b"audit", {"on_header_value": lambda data, start, end: header_bytes.append(end-start)})
    header = b"x" * 32768
    parser.write(b"--audit\r\nX-Audit: " + header + b"\r\n\r\nx\r\n--audit--\r\n")
    rows["multipart_oversized_part_header"] = {"header_value_bytes": len(header), "accepted_callback_bytes": sum(header_bytes)}

    _, options = parse_options_header(b"form-data; name=comment; name*=utf-8''role")
    rows["multipart_extended_name"] = {"parsed_name": options.get(b"name", b"").decode()}

    # Demonstrate route-to-resolver reachability while preventing all path I/O.
    with tempfile.TemporaryDirectory(prefix="dependency-static-", dir=ROOT / "audit") as folder:
        app = Starlette(routes=[Mount("/uploads", StaticFiles(directory=folder), name="uploads")])
        attempted = []
        original_realpath = __import__("os").path.realpath
        def intercepted(path, *args, **kwargs):
            if str(path).startswith("\\\\"):
                attempted.append(str(path))
                raise FileNotFoundError("audit blocked UNC resolution before any I/O")
            return original_realpath(path, *args, **kwargs)
        with patch("starlette.staticfiles.os.path.realpath", side_effect=intercepted):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://audit.local") as client:
                try:
                    await client.get("/uploads/%5C%5Caudit.invalid%5Cshare%5Cfile")
                except FileNotFoundError:
                    pass  # The instrumentation stops the resolver before I/O.
        rows["windows_static_unc_resolution_intercepted"] = {"unc_resolver_calls_blocked": len(attempted), "outbound_smb_attempted": False}

    (ROOT / "audit" / "dependency-behaviors.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(json.dumps(rows, indent=2))


asyncio.run(main())
