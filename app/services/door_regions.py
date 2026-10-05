"""Bounded, local sign proposals. Geometry proposes regions; OCR must still read digits."""
import math


def rectify_region(array, points):
    import cv2
    import numpy as np
    points=np.asarray(points,dtype=np.float32)
    if points.shape!=(4,2) or not np.isfinite(points).all():return None
    # Sort around the centre, then start at top-left. The long edge is horizontal.
    centre=points.mean(axis=0)
    points=points[np.argsort(np.arctan2(points[:,1]-centre[1],points[:,0]-centre[0]))]
    points=np.roll(points,-np.argmin(points.sum(axis=1)),axis=0)
    width=max(np.linalg.norm(points[1]-points[0]),np.linalg.norm(points[2]-points[3]))
    height=max(np.linalg.norm(points[3]-points[0]),np.linalg.norm(points[2]-points[1]))
    if height>width:points=np.roll(points,-1,axis=0);width,height=height,width
    width,height=round(float(width)),round(float(height))
    if width<24 or height<12 or width>array.shape[1]*2 or height>array.shape[0]*2:return None
    # Modest padding around the detected line keeps glyph edges from being clipped.
    points=centre+(points-centre)*1.06
    matrix=cv2.getPerspectiveTransform(points.astype(np.float32),np.float32([[0,0],[width-1,0],[width-1,height-1],[0,height-1]]))
    return cv2.warpPerspective(array,matrix,(width,height),borderMode=cv2.BORDER_REPLICATE)


def visual_sign_regions(array,limit=4):
    import cv2
    import numpy as np
    height,width=array.shape[:2]
    scale=min(1,1000/max(height,width))
    small=cv2.resize(array,None,fx=scale,fy=scale) if scale<1 else array
    grey=cv2.cvtColor(small,cv2.COLOR_BGR2GRAY)
    edges=cv2.Canny(cv2.GaussianBlur(grey,(3,3),0),45,130)
    edges=cv2.morphologyEx(edges,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    contours,_=cv2.findContours(edges,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
    candidates=[]
    for contour in contours:
        (_, _),(w,h),_=cv2.minAreaRect(contour)
        short,long=sorted((w,h));area=w*h
        if short<12*scale or long<45*scale or not .0008*grey.size<area<.18*grey.size:continue
        aspect=long/short
        if not 1.8<aspect<9:continue
        fill=abs(cv2.contourArea(contour))/area
        if fill<.62:continue
        approx=cv2.approxPolyDP(contour,.025*cv2.arcLength(contour,True),True)
        points=approx.reshape(4,2) if len(approx)==4 else cv2.boxPoints(cv2.minAreaRect(contour))
        points=points.astype(float)/scale
        x0,y0=points.min(axis=0);x1,y1=points.max(axis=0)
        if x1-x0<y1-y0:continue
        score=fill*math.exp(-abs(math.log(aspect/4)))
        candidates.append((score,points,(x0,y0,x1,y1)))
    selected=[]
    for _,points,box in sorted(candidates,key=lambda item:-item[0]):
        x0,y0,x1,y1=box
        def overlap(other):
            a,b,c,d=other
            intersection=max(0,min(x1,c)-max(x0,a))*max(0,min(y1,d)-max(y0,b))
            return intersection/max(1,min((x1-x0)*(y1-y0),(c-a)*(d-b)))
        if any(overlap(b)>.7 for _,b in selected):continue
        selected.append((points,box))
        if len(selected)>=limit:break
    return [points for points,_ in selected]


def normalized_region(points,width,height,method):
    x0,y0=points.min(axis=0);x1,y1=points.max(axis=0)
    x0,y0=max(0,float(x0)),max(0,float(y0));x1,y1=min(width,float(x1)),min(height,float(y1))
    return {'x':x0/width,'y':y0/height,'width':(x1-x0)/width,'height':(y1-y0)/height,'method':method}
