from pathlib import Path
import inspect
import fg_env
from fg_env.guides import guide_parts
root=Path(__file__).resolve().parents[1]
out=root/'docs/sdk'
parts=[p for p in guide_parts() if p!='all']
index=['# Contract reference','','Generated from the installed source models, expression registry and authoring guides. Regenerate with `python scripts/build_docs_reference.py`.','','[Python API](api.md) · [Authoring workflow](authoring.md)','','## Guide sections','']
for part in parts:
 text=fg_env.guide(part)
 title=part.replace('.',' / ')
 (out/f"reference-{part.replace('.', '-')}.md").write_text(f'# {title}\n\n'+text+'\n')
 index.append(f'- [{title}](reference-{part.replace(chr(46), chr(45))}.md)')
(out/'reference.md').write_text('\n'.join(index)+'\n')
api=['# Python API reference','','Generated from public exports in `fg_env`. Start with `check`, `load`, `run` and `experiment`; everything else is in a subpackage below. A source may be a contract dictionary, JSON text, file path or parsed `Contract`.','','## Entry points','']
def entry(heading,name,obj):
 out=[f'{heading} `{name}`','']
 try: out += ['```python',f'{name.rpartition(".")[2]}{inspect.signature(obj)}','```','']
 except (ValueError,TypeError): pass
 doc=inspect.getdoc(obj)
 return out+([doc,''] if doc else [])
public=lambda obj: inspect.isfunction(obj) or inspect.isclass(obj)
for name in fg_env.__all__:
 obj=getattr(fg_env,name)
 if public(obj): api += entry('##',name,obj)
for name in fg_env.__all__:
 module=getattr(fg_env,name)
 if inspect.ismodule(module):
  api += [f'## `fg_env.{name}`','',inspect.getdoc(module) or '','']
  for export in module.__all__:
   obj=getattr(module,export)
   if public(obj): api += entry('###',f'{name}.{export}',obj)
api += ['## Environment methods','']
for name in ['preview','run','arun','step','snapshot','restore','clone','fork','entity','entities','result','spectate']:
 obj=getattr(fg_env.Env,name)
 api += [f'### `Env.{name}`','','```python',f'{name}{inspect.signature(obj)}','```','',inspect.getdoc(obj) or '', '']
api += ['## Detailed runtime behavior','','See [running](reference-running.md) for participants, budgets, traces, snapshots and experiments.']
(out/'api.md').write_text('\n'.join(api)+'\n')
(root/'scripts').mkdir(exist_ok=True)
print(f'{len(parts)} guide pages and public API generated')
