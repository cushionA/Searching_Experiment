#!/usr/bin/env python3
"""Generate a fully synthetic text-layer PDF for local CLI mode comparison."""
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from pathlib import Path

out = Path('benchmarks/daily-tech-20261012/synthetic-200page-text.pdf')
c = canvas.Canvas(str(out), pagesize=letter, pageCompression=1)
for page in range(1, 201):
    c.setFont('Helvetica', 9)
    y = 750
    for line in range(1, 31):
        c.drawString(48, y, f'Page {page:04d} line {line:02d}: Synthetic document conversion text. Repeatable fixture only.')
        y -= 22
    c.showPage()
c.save()
print(out, out.stat().st_size)
