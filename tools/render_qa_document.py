"""Use the packaged DOCX renderer, with native Word conversion on this host."""
import importlib.util
from pathlib import Path
import subprocess

APP=Path(__file__).resolve().parents[1]
RENDERER=Path('C:/Users/1/.codex/plugins/cache/openai-primary-runtime/documents/26.1011.51657/skills/documents/render_docx.py')
spec=importlib.util.spec_from_file_location('packaged_docx_renderer',RENDERER)
renderer=importlib.util.module_from_spec(spec);spec.loader.exec_module(renderer)


def convert_with_word(doc_path,user_profile,convert_tmp_dir,stem,verbose):
    destination=Path(convert_tmp_dir)/(stem+'.pdf')
    result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(APP/'tools/render_export_with_word.ps1'),
                           '-InputDocument',doc_path,'-OutputPdf',str(destination)],capture_output=True,text=True,
                           encoding='utf-8',errors='replace',timeout=240)
    if result.returncode or not destination.is_file():
        raise RuntimeError('Native Word rendering failed: '+(result.stdout or '')+(result.stderr or ''))
    return str(destination),result.stdout


renderer.convert_to_pdf=convert_with_word
renderer.main()
