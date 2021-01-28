import random
from flask import Flask
from flask import render_template, request, url_for
import os
import glob
from sqlitedict import SqliteDict
import json
import datetime
import difflib
import html
import hashlib
import re

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

DATADIR=os.environ["PARAANN_DATA"]
APP_ROOT=os.environ.get("PICK_PARA_ROOT","")

def matches(s1,s2,minlen=5):
    m=difflib.SequenceMatcher(None,s1,s2,autojunk=False)

    #returns list of (idx1,idx2,len) perfect matches
    return sorted(matches_r(m,s1,s2,minlen,0,len(s1),0,len(s2)),
                  key=lambda match: (match[2], match[0]))

def matches_r(m,s1,s2,min_len,s1_beg,s1_end,s2_beg,s2_end):
    lm=m.find_longest_match(s1_beg,s1_end,s2_beg,s2_end)
    #with open("log.txt","a") as f:
        #print(">>>> CHECKED",file=f)
        #print(s1[s1_beg:s1_end],file=f)
        #print("---",file=f)
        #print(s2[s2_beg:s2_end],file=f)
        #print(">>>> FOUND",file=f)
        #print(s1[lm.a:lm.a+lm.size],file=f)
        #print("\n",file=f)
    #s1[lm.a:lm.a+lm.size]
    #s2[lm.b:lm.b+lm.size]
    if lm.size<min_len:
        return []
    else:
        s1_left=s1_beg,lm.a
        s1_right=lm.a+lm.size,s1_end
        s1_all=(s1_beg,s1_end)
        
        s2_left=s2_beg,lm.b
        s2_right=lm.b+lm.size,s2_end
        s2_all=(s2_beg,s2_end)
        
        matches=[(lm.a,lm.b,lm.size)]
        for i1,i2 in ((s1_left,s2_left),(s1_left,s2_right),(s1_right,s2_left),(s1_right,s2_right)):
            #try all combinations of what remains
            if i1[1]-i1[0]<min_len:
                continue #too short to produce match
            if i2[1]-i2[0]<min_len:
                continue #too short to produce match
            sub=matches_r(m,s1,s2,min_len,*i1,*i2)
            matches.extend(sub)
        return matches

def build_spans(s,blocks):
    """s:string, blocks are pairs of (idx,len) of perfect matches"""
    #allright, this is pretty dumb alg!
    matched_indices=[0]*len(s)
    for i,l in blocks:
        for idx in range(i,i+l):
            matched_indices[idx]=max(matched_indices[idx],l)
    spandata=[]
    for c,matched_len in zip(s,matched_indices):
        #matched_len=(matched_len//5)*5
        if not spandata or spandata[-1][1]!=matched_len: #first or span with opposite match polarity -> must make new!
            spandata.append(([],matched_len))
        spandata[-1][0].append(c)
    merged_spans=[(html.escape("".join(chars)),matched_len) for chars,matched_len in spandata]
    return merged_spans, min(matched_indices),max(matched_indices) #min is actually always 0, but it's here for future need

#matches("Minulla on koira mutta sinulla on kissa.","Sinulla on kissa ja minulla on koira.")

def read_batches():
    batchdict={} #user -> batchfile -> Batch
    batchfiles=sorted(glob.glob(DATADIR+"/batches-*/*.json"))
    for b in batchfiles:
        dirname,fname=b.split("/")[-2:]
        user=dirname.replace("batches-","")
        batchdict.setdefault(user,{})[fname]=Batch(b)
    return batchdict
        
       
class Batch:

    def __init__(self,batchfile):
        self.batchfile=batchfile
        with open(batchfile) as f:
            self.data=json.load(f) #this is a list of document pairs to annotate
            if isinstance(self.data, list):
                # old version without movie level metadata
                # create metadata on the fly
                self.data = {"id": os.path.basename(batchfile), "name": "", "annotation_ready": False, "segments": self.data}
        self.filetime = os.path.getmtime(batchfile)

    def save(self):
        s=json.dumps(self.data,ensure_ascii=False,indent=2,sort_keys=True)
        with open(self.batchfile,"wt") as f:
            print(s,file=f)

    @property
    def get_anno_stats(self):
        extracted=0
        touched=0
        if "_r" in self.data["id"]: # new rounds
            for pair in self.data["segments"]:
                # not taking into account the candidates picked in the previous rounds
                if not pair["locked"] and "annotation" in pair and pair["annotation"]:
                    extracted+=len(pair["annotation"])
                    touched+=1
        else: # old rounds
            for pair in self.data["segments"]:
                if "annotation" in pair and pair["annotation"]:
                    extracted+=len(pair["annotation"])
                    touched+=1
        return (touched,extracted) #how many pairs touched, how many examples extracted total
    
    def get_update_timestamp(self):
        timestamps=[pair.get("updated") for pair in self.data["segments"]]
        timestamps=[stamp for stamp in timestamps if stamp]
        timestamps = [datetime.datetime.fromisoformat(stamp) for stamp in timestamps]
        print("TS",timestamps)
        if not timestamps:
            return "no updates"
        else:
            return max(timestamps).isoformat()
            
def sort_batches(batches):
    # batches: list of ('01427.json', <paraanno.app.Batch object at 0x7ff45e243d60>)
    no_timestamps = []
    with_timestamps = []
    for b in batches:
        if b[1].get_update_timestamp()=="no updates":
            no_timestamps.append(b)
        else:
            with_timestamps.append(b)
    no_timestamps = sorted(no_timestamps, key=lambda x:x[1].filetime)
    with_timestamps = sorted(with_timestamps, key=lambda x:x[1].get_update_timestamp())
    return with_timestamps + no_timestamps

def init():
    global all_batches
    global textdbs
    all_batches=read_batches()
    textdbs={}
    try:
        for src in SqliteDict.get_tablenames(DATADIR+"/all-texts.sqlited"):
            textdbs[src]=SqliteDict(DATADIR+"/all-texts.sqlited",tablename=src)
    except OSError: # new format 201028: text in json file
        pass
    print(list(textdbs.keys()))

init()            

@app.route('/')
def hello_world():
    global all_batches

    # dict user -> (no.of ready movies, no. of not ready movies)
    movie_stats = {}
    for user, movies in all_batches.items():
        ready = 0
        not_ready = 0
        for m in movies.values():
            if m.data["annotation_ready"] == True:
                ready += 1
            else:
                not_ready += 1
        movie_stats[user] = (ready, not_ready)
    #print(movie_stats)
    return render_template("index.html",
                           app_root=APP_ROOT,
                           users=sorted(all_batches.keys()),
                           stats=movie_stats)

@app.route("/ann/<user>")
def batchlist(user):
    global all_batches
    user_batches=[]
    for batchfile, b in sort_batches(all_batches[user].items()):
        ann_ready = "checked" if b.data["annotation_ready"] == True else ""
        movie_name = b.data["name"].replace("\\","")
        user_batches.append((batchfile, b, movie_name, ann_ready))
    return render_template("batch_list.html",app_root=APP_ROOT,batches=user_batches,user=user)

@app.route("/ann/<user>/<batchfile>")
def jobsinbatch(user,batchfile):
    global all_batches
    global textdbs
    pairs=all_batches[user][batchfile].data["segments"]
    pairdata=[]
    for idx,pair in enumerate(pairs):
        src1,f1=pair["d1"]
        src2,f2=pair["d2"]
        try:
            text1=textdbs[src1][f1]
            text2=textdbs[src2][f2]
        except KeyError: # new format, text in json file
            text1=all_batches[user][batchfile].data["segments"][idx]["d1_text"]
            text2=all_batches[user][batchfile].data["segments"][idx]["d2_text"]
        try: # new rounds
            previous_annotator = all_batches[user][batchfile].data["segments"][idx]["annotator"]
        except KeyError: # old rounds
            previous_annotator = ""
        pairdata.append((idx,pair.get("updated","not updated"),text1[:100],text2[:100], previous_annotator))
    h=hashlib.sha256((batchfile).encode("utf-8")).digest()[:2] #first two bytes of the digest will do
    seed=int.from_bytes(h,"little")
    random.seed(seed) #guarantees stable order for this batch across users
    random.shuffle(pairdata)
    return render_template("doc_list_in_batch.html",app_root=APP_ROOT,user=user,batchfile=batchfile,pairdata=pairdata)

@app.route("/saveann/<user>/<batchfile>/<pairseq>",methods=["POST"])
def save_document(user,batchfile,pairseq):
    global all_batches
    pairseq=int(pairseq)
    annotation=request.json
    pair=all_batches[user][batchfile].data["segments"][pairseq]
    pair["updated"]=datetime.datetime.now().isoformat()
    pair["annotation"]=annotation
    all_batches[user][batchfile].save()
    return "",200
    
@app.route("/savebatchstatus/<user>",methods=["POST"])
def save_batchlist(user):
    global all_batches
    anns=request.json
    for batchfile, status in anns.items():
        if status != all_batches[user][batchfile].data["annotation_ready"]:
            print("Saving status:",batchfile, status)
            all_batches[user][batchfile].data["annotation_ready"] = status
            all_batches[user][batchfile].save()
    return "",200

@app.route("/ann/<user>/<batchfile>/<pairseq>")
def fetch_document(user,batchfile,pairseq):
    global all_batches
    global textdbs
    pairseq=int(pairseq)
    pair=all_batches[user][batchfile].data["segments"][pairseq]
    try:
        locked = pair["locked"]
    except KeyError: # old rounds
        locked = False
    # {
    # "d1": [
    #   "hs",
    #   "2020-01-08-23-00-04---84c7baba125d4592e300ffbe5e04396a.txt"
    # ],
    # "d2": [
    #   "yle",
    #   "2020-01-08-21-03-47--3-11148909.txt"
    # ],
    # "sim": 0.9922545481090577
    # }

    src1,f1=pair["d1"]
    src2,f2=pair["d2"]
    try:
        text1=textdbs[src1][f1]
        text2=textdbs[src2][f2]
    except KeyError: # new format, text in json file
        text1=all_batches[user][batchfile].data["segments"][pairseq]["d1_text"]
        text2=all_batches[user][batchfile].data["segments"][pairseq]["d2_text"]

    text1=re.sub(r"\n+","\n",text1)
    text2=re.sub(r"\n+","\n",text2)

    text1=text1.replace("<i>"," ").replace("</i>"," ")
    text2=text2.replace("<i>"," ").replace("</i>"," ")

    text1=re.sub(r" +"," ",text1)
    text2=re.sub(r" +"," ",text2)

    
    blocks=matches(text1,text2,15) #matches are (idx1,idx2,len)
    spandata1,min1,max1=build_spans(text1,list((b[0],b[2]) for b in blocks))
    spandata2,min2,max2=build_spans(text2,list((b[1],b[2]) for b in blocks))
    
    
    annotation=pair.get("annotation",[])
    
    return render_template("doc.html",app_root=APP_ROOT,locked=locked,left_text=text1,right_text=text2,left_spandata=spandata1,right_spandata=spandata2,pairseq=pairseq,batchfile=batchfile,user=user,annotation=annotation,min_mlen=min(min1,min2),max_mlen=max(max1,max2)+1,mlenv=min(max(max1,max2),30),is_last=(pairseq==len(all_batches[user][batchfile].data["segments"])-1))

