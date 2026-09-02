import json, urllib.request, io, sys
from PIL import Image, ImageDraw
JF="http://192.168.9.50:8096"; KEY=open("/etc/media-core/jellyfin-prod.key").read().strip()
auth={"Authorization":"MediaBrowser Token="+KEY}
def get(u):
    return urllib.request.urlopen(urllib.request.Request(u,headers=auth),timeout=45).read()
LO,HI,OUT=int(sys.argv[1]),int(sys.argv[2]),sys.argv[3]
items=json.loads(get(JF+"/LiveTv/Channels?limit=2000"))["Items"]
want=sorted([i for i in items if i.get("ChannelNumber") and i["ChannelNumber"].isdigit()
             and LO<=int(i["ChannelNumber"])<=HI], key=lambda i:int(i["ChannelNumber"]))
CELL,COLS=170,7
rows=(len(want)+COLS-1)//COLS
sheet=Image.new("RGB",(CELL*COLS,(CELL+22)*rows),(30,30,30)); d=ImageDraw.Draw(sheet)
for k,it in enumerate(want):
    x,y=(k%COLS)*CELL,(k//COLS)*(CELL+22)
    try:
        im=Image.open(io.BytesIO(get("%s/Items/%s/Images/Primary"%(JF,it["Id"])))).convert("RGB")
        im.thumbnail((CELL-8,CELL-8),Image.LANCZOS)
        sheet.paste(im,(x+(CELL-im.width)//2,y+(CELL-im.height)//2))
    except Exception:
        d.text((x+6,y+70),"ERR",fill=(255,80,80))
    d.text((x+3,y+CELL+4),("%s %s"%(it["ChannelNumber"],it["Name"].split(": ",1)[-1]))[:30],fill=(215,215,215))
sheet.save(OUT); print("wrote",OUT,len(want))
