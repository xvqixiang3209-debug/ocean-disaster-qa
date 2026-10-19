"""Place page PNGs in 2x2 QA grids without resizing any source pixels."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from PIL import Image,ImageDraw,ImageFont

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('render',type=Path)
    ap.add_argument('--output',type=Path)
    args=ap.parse_args()
    output=args.output or args.render/'grids_100dpi'
    output.mkdir(parents=True,exist_ok=True)
    paths=sorted(args.render.glob('page-*.png'),key=lambda p:int(re.search(r'page-(\d+)',p.name).group(1)))
    assert paths
    margin=22; label_height=30
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',18)
    manifest=[]
    for offset in range(0,len(paths),4):
        selected=paths[offset:offset+4]
        images=[Image.open(path).convert('RGB') for path in selected]
        width=max(image.width for image in images);height=max(image.height for image in images)
        canvas=Image.new('RGB',(width*2+margin*3,(height+label_height)*2+margin*3),'white')
        draw=ImageDraw.Draw(canvas)
        entries=[]
        for index,(path,img) in enumerate(zip(selected,images)):
            x=margin+(index%2)*(width+margin)
            y=margin+(index//2)*(height+label_height+margin)
            number=int(re.search(r'page-(\d+)',path.name).group(1))
            draw.text((x,y),f'Page {number}  |  100 dpi  |  source pixels unchanged',font=font,fill='black')
            canvas.paste(img,(x,y+label_height))
            # Verify every source pixel is present at identical dimensions.
            crop=canvas.crop((x,y+label_height,x+img.width,y+label_height+img.height))
            assert crop.tobytes()==img.tobytes()
            entries.append({'page':number,'source':str(path),'width':img.width,'height':img.height,'pixel_copy':'identical'})
        destination=output/f'grid-{offset//4+1:03d}.png'
        canvas.save(destination,dpi=(100,100))
        manifest.append({'grid':str(destination),'pages':[entry['page'] for entry in entries],'entries':entries,'canvas_width':canvas.width,'canvas_height':canvas.height})
        for img in images:img.close()
    result={'render_dir':str(args.render),'grid_dir':str(output),'page_count':len(paths),'grid_count':len(manifest),'dpi':100,'rescaling':False,'grids':manifest}
    (output/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in result.items() if key!='grids'},ensure_ascii=False))

if __name__=='__main__':main()
