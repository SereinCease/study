export const motionShader = `
uniform float uTime,uBreath,uSleeve,uStars,uWeather,uMotionGain;
float ellipse(vec2 p,vec2 c,vec2 r){vec2 q=(p-c)/r;return exp(-dot(q,q)*2.0);}
vec2 animateFigure(vec2 uv){
  vec2 px=vec2(uv.x,1.0-uv.y)*vec2(1536.0,1024.0);
  float book=1.0-smoothstep(1.0,1.45,length((px-vec2(1015.0,552.0))/vec2(170.0,100.0)));
  float hand=1.0-smoothstep(.85,1.35,length((px-vec2(1278.0,450.0))/vec2(90.0,55.0)));
  float breath=sin(uTime*1.12);
  // Confine breathing to chest / shoulders; the seated lower body is anchored.
  float chest=ellipse(px,vec2(1005.0,350.0),vec2(140.0,115.0));
  // GLSL requires edge0 < edge1: use the complement for a descending weight.
  float waistAnchor=1.0-smoothstep(370.0,440.0,px.y);
  chest*=waistAnchor;
  vec2 shift=vec2((px.x-1005.0)*.012,2.6)*chest*breath*uBreath;
  // Sleeve motion stays independent from the breathing waist anchor.
  float sleeve=ellipse(px,vec2(1194.0,602.0),vec2(115.0,135.0));
  float left=ellipse(px,vec2(790.0,650.0),vec2(125.0,85.0));
  shift+=vec2(8.0*sin(uTime*.78+.6),2.3*sin(uTime*.92))*sleeve*uSleeve;
  shift+=vec2(4.5*sin(uTime*.78),1.5*sin(uTime*.92))*left*uSleeve;
  shift*=uMotionGain*(1.0-book)*(1.0-hand);
  return uv+shift/vec2(1536.0,-1024.0);
}

// Conservative interior window mask: atmospheric layers sit behind the figure.
// Keep the wood frame, indoor furniture and the moon itself untouched.
float windowMask(vec2 p){
 float d=length((p-vec2(1180.0,180.0))/vec2(300.0,185.0));
 return (1.0-smoothstep(.88,1.0,d))*smoothstep(20.0,50.0,p.y);
}
vec3 windowAtmosphere(vec3 base,vec2 uv){
 if(uWeather<.5)return base;
 vec2 p=vec2(uv.x,1.0-uv.y)*vec2(1536.0,1024.0);
 float mask=windowMask(p);
 if(mask<.001)return base;
 // A few slow, asynchronous star pulses, not a flashing screen overlay.
 float shine=0.0;
 for(int i=0;i<14;i++){
  float k=float(i);
  vec2 center=vec2(1130.0+mod(k*65.0,240.0),40.0+mod(k*37.0,110.0));
  vec2 q=p-center;
  float pulse=pow(.5+.5*sin(uTime*(.65+mod(k,.3))+k*2.3),3.0);
  shine+=exp(-dot(q,q)/3.0)*pulse*.34;
  shine+=exp(-dot(q,q)/32.0)*pulse*.035;
 }
 base+=vec3(.65,.77,1.0)*shine*mask*uMotionGain;
 // Six small maple silhouettes follow independent falling / flutter paths.
 for(int i=0;i<6;i++){
  float k=float(i),life=fract(uTime/(16.0+k*2.2)+k*.173);
  vec2 center=vec2(960.0+mod(k*71.0,350.0)+28.0*sin(life*6.28+k),55.0+life*285.0);
  float angle=sin(uTime*.8+k)*.65+k;
  vec2 q=p-center;
  q=mat2(cos(angle),-sin(angle),sin(angle),cos(angle))*q;
  q.x/= .65+.35*abs(sin(uTime*1.1+k));
  float theta=atan(q.y,q.x),radius=length(q);
  float edge=(5.8+mod(k,3.0))*(.67+.22*cos(theta*5.0)+.11*sin(theta));
  float leaf=1.0-smoothstep(edge-.65,edge+.65,radius);
  float fade=smoothstep(0.0,.12,life)*(1.0-smoothstep(.82,1.0,life));
  vec3 pigment=mix(vec3(.24,.055,.025),vec3(.48,.17,.045),.5+.5*sin(k));
  base=mix(base,pigment,leaf*fade*mask*.82*min(1.0,uMotionGain));
 }
 return base;
}

float segmentDistance(vec2 p,vec2 a,vec2 b){vec2 ab=b-a;return length(p-a-ab*clamp(dot(p-a,ab)/dot(ab,ab),0.0,1.0));}
vec2 starPoint(int i){
 if(i==0)return vec2(.12,.55);if(i==1)return vec2(.26,.35);
 if(i==2)return vec2(.40,.49);if(i==3)return vec2(.52,.24);
 if(i==4)return vec2(.64,.42);if(i==5)return vec2(.80,.27);
 if(i==6)return vec2(.88,.51);if(i==7)return vec2(.73,.69);
 if(i==8)return vec2(.51,.62);return vec2(.34,.78);
}
vec3 flowingPaper(vec3 base,vec2 uv){
 // A conservative inner quadrilateral of the paper, away from inkstone and brush.
 vec2 top=vec2(uv.x,1.0-uv.y);
 float y=(top.y-(.708+.115*top.x))/.155;
 float x=(top.x-(.337-.070*y))/(.377+.020*y);
 vec2 p=vec2(x,y);
 if(x<0.0||x>1.0||y<0.0||y>1.0||uStars<.5)return base;
 float edge=smoothstep(0.0,.12,x)*smoothstep(0.0,.12,1.0-x)*smoothstep(0.0,.16,y)*smoothstep(0.0,.16,1.0-y);
 float ink=(sin(p.x*19.0+p.y*7.0+sin(p.y*9.0-uTime*.09))+sin(p.y*15.0-p.x*8.0+uTime*.07))*.25+.5;
 float cloud=ellipse(p,vec2(.5,.5),vec2(.48,.40))*ink;
 base*=1.0-.075*cloud*(.65+.35*sin(uTime*.35));
 float glow=0.0;
 for(int i=0;i<9;i++){
  vec2 a=starPoint(i),b=starPoint(i+1);
  float line=segmentDistance(p,a,b);
  float progress=fract(uTime*.045+float(i)*.173);
  float bead=length(p-mix(a,b,progress));
  float star=length(p-a);
  glow+=.12*exp(-line*line/0.000012)*(.6+.4*sin(uTime*.5+float(i)));
  glow+=.55*exp(-bead*bead/0.00006);
  glow+=.30*exp(-star*star/0.00006)*(.65+.35*sin(uTime*.8+float(i)*1.7));
 }
 return base+vec3(.38,.23,.068)*glow*edge;
}
`;
