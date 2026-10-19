"""Unscaled 2x2 pages for complete visual layout review."""
import argparse,json,re
from pathlib import Path
from PIL import Image,ImageDraw


def main():
    p=argparse.ArgumentParser();p.add_argument('render',type=Path);a=p.parse_args()
    pages=sorted(a.render.glob('page-*.png'),key=lambda x:int(re.search(r'-(\d+)\.png$',x.name)[1]))
    if not pages:pages=sorted(a.render.glob('*.png'),key=lambda x:int(re.search(r'-(\d+)\.png$',x.name)[1]))
    out=a.render.parent/(a.render.name+'_sheets');out.mkdir(exist_ok=True);items=[]
    for start in range(0,len(pages),4):
        group=pages[start:start+4];images=[Image.open(f).convert('RGB') for f in group]
        w=max(i.width for i in images);h=max(i.height for i in images)
        sheet=Image.new('RGB',(w*2+24,(h+28)*2+24),'#dddddd');draw=ImageDraw.Draw(sheet)
        for k,im in enumerate(images):
            x=12+(k%2)*w;y=12+(k//2)*(h+28)
            draw.text((x+8,y+6),f'Page {start+k+1}',fill='black');sheet.paste(im,(x,y+28))
        target=out/f'sheet-{start//4+1:03}.png';sheet.save(target)
        items.append({'sheet':str(target.resolve()),'pages':list(range(start+1,start+len(group)+1))})
    (out/'manifest.json').write_text(json.dumps({'page_count':len(pages),'unscaled':True,'items':items},indent=2),encoding='utf8')
    print(len(pages),'pages',len(items),'sheets')


if __name__=='__main__':main()
