import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(repo / 'src'))

from prisma.generator import Generator

Generator.invoke()
