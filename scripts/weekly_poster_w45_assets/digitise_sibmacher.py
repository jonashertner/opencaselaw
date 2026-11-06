"""Read a Sibmacher pattern-book page cell by cell: track the engraved grid lines in a few strips
(the page is warped), then take each cell's mean darkness. Usage: grid.py img x0 x1 y0 y1 px py out.npy"""
import numpy as np, sys
from PIL import Image
src=sys.argv[1]; X0,X1,Y0,Y1=[int(v) for v in sys.argv[2:6]]; PX,PY=float(sys.argv[6]),float(sys.argv[7]); out=sys.argv[8]
im=255-np.asarray(Image.open(src).convert('L')).astype(float)
reg=im[Y0:Y1,X0:X1]; H,W=reg.shape
def track(prof,pitch,start=None):
    d=prof-np.convolve(prof,np.ones(9)/9,'same')
    if start is None: start=int(np.argmax(d[:int(pitch)+2]))
    else: start=max(0,start-3)+int(np.argmax(d[max(0,start-3):start+4]))
    o=[start]
    while o[-1]+pitch+3<len(d):
        e=o[-1]+pitch; lo,hi=int(round(e))-2,int(round(e))+3; o.append(lo+int(np.argmax(d[lo:hi])))
    return np.array(o)
def strips(axis,pitch,n):
    L=H if axis==0 else W; res=[]
    first=track(reg.mean(0) if axis==0 else reg.mean(1),pitch)[0]   # one start for every strip
    for s in range(n):
        a,b=s*L//n,(s+1)*L//n
        res.append(track(reg[a:b].mean(0) if axis==0 else reg[:,a:b].mean(1),pitch,first))
    k=min(len(r) for r in res); return np.array([r[:k] for r in res],float),[(s+.5)*L/n for s in range(n)]
cl,cyc=strips(0,PX,3); rl,rxc=strips(1,PY,3)
print('lines',cl.shape,rl.shape,'spread',np.ptp(cl,0).max(),np.ptp(rl,0).max())
G=np.zeros((rl.shape[1]-1,cl.shape[1]-1))
for a in range(G.shape[0]):
    for b in range(G.shape[1]):
        y=(rl[:,a].mean()+rl[:,a+1].mean())/2; x0=np.interp(y,cyc,cl[:,b]); x1=np.interp(y,cyc,cl[:,b+1])
        x=(x0+x1)/2; y0=np.interp(x,rxc,rl[:,a]); y1=np.interp(x,rxc,rl[:,a+1])
        xs=np.arange(int(x0)+3,int(x1)-2); ys=np.arange(int(y0)+3,int(y1)-2)
        G[a,b]=reg[np.ix_(ys,xs)].mean()
np.save(out,G); print(G.shape,np.histogram(G,bins=14,range=(0,255))[0])
