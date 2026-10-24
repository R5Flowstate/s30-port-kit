import sys,struct,os,glob
src,dst=sys.argv[1],sys.argv[2]
os.makedirs(dst,exist_ok=True)
n=0
for f in glob.glob(os.path.join(src,'*.msw')):
    b=open(f,'rb').read(); g=os.path.basename(f)[:-4]; i=0; k=0
    while True:
        i=b.find(b'DXBC',i)
        if i<0: break
        sz=struct.unpack_from('<I',b,i+24)[0]
        if 32<sz<=len(b)-i:
            open(os.path.join(dst,f'{g}_{k}.dxbc'),'wb').write(b[i:i+sz]); k+=1; n+=1; i+=sz
        else: i+=4
print(n)
