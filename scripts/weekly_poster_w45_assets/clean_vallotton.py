from PIL import Image, ImageFilter
import numpy as np, sys
src,out=sys.argv[1],sys.argv[2]
MED,THR=3,100
PROTECT=[(250,720,420,960),(880,1000,1110,1180)]   # faces (x0,y0,x1,y1) in crop px
im=Image.open(src).convert('L'); k=im.size[0]/1200
c=im.crop((int(233*k),int(119*k),int(681*k),int(619*k))).filter(ImageFilter.MedianFilter(MED))
blk=np.asarray(c)<=THR
H,W=blk.shape
def comps(mask):
    lab=np.zeros(H*W,np.int32); info=[None]; flat=mask.ravel(); n=0
    for s in np.flatnonzero(flat):
        if lab[s]: continue
        n+=1; lab[s]=n; q=[s]; cnt=0; x0=y0=10**9; x1=y1=-1
        while q:
            p=q.pop(); cnt+=1; y,x=divmod(p,W)
            x0=min(x0,x);x1=max(x1,x);y0=min(y0,y);y1=max(y1,y)
            for qq in ((p-1) if x>0 else -1,(p+1) if x<W-1 else -1,(p-W) if y>0 else -1,(p+W) if y<H-1 else -1):
                if qq>=0 and flat[qq] and not lab[qq]: lab[qq]=n; q.append(qq)
        info.append((cnt,x0,y0,x1,y1))
    return lab.reshape(H,W),info
def prot(x0,y0,x1,y1): return any(px0<=x0 and x1<=px1 and py0<=y0 and y1<=py1 for px0,py0,px1,py1 in PROTECT)
def drop(info,small,elong):
    d=np.zeros(len(info),bool)
    for i,(cnt,x0,y0,x1,y1) in enumerate(info[1:],1):
        if prot(x0,y0,x1,y1): continue
        bw,bh=x1-x0+1,y1-y0+1; ratio=max(bw,bh)/min(bw,bh); fill=cnt/(bw*bh)
        if cnt<small or (cnt<elong and (ratio>2.2 or fill<.35)): d[i]=True
    return d
lab,info=comps(blk); d=drop(info,300,1500); blk=blk&~d[lab]
lab,info=comps(~blk); d=drop(info,40,900); blk=blk|d[lab]
Image.fromarray(np.where(blk,0,255).astype('uint8')).save(out)
