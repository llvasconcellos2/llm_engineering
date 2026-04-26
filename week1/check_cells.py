import json
import ast

with open('c:/Users/leona/Projects/LLM_ENGINEERING/llm_engineering/week1/day1.ipynb', 'r') as f:
    nb = json.load(f)

for i, cell in enumerate(nb['cells']):
    if cell['cell_type'] == 'code':
        source = "".join(cell['source'])
        if source.strip():
            try:
                ast.parse(source)
            except Exception as e:
                print(f"Cell {i} has syntax error: {e}")
