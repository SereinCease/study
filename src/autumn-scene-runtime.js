import * as THREE from 'three';
import {motionShader} from './autumn-motion-shader';
const fragmentShader = `
precision highp float;
varying vec2 vUv;
uniform sampler2D uBackground,uCharacter,uNormal,uReference;
uniform vec4 uRegion;
uniform int uMode;
uniform float uCool,uWarm,uRim,uShadow;
uniform vec3 uLight;
uniform vec2 uPixel;
${motionShader}
// The image service re-framed the extracted figure. This shared UV transform
// restores the seated scale and anchor; source PNGs remain untouched.
vec2 characterUv(vec2 uv){return (uv-vec2(449.0/1536.0,1.0-.63-106.0/1024.0))/.63;}
float characterAlpha(vec2 uv){
  if(any(lessThan(uv,vec2(0.0)))||any(greaterThan(uv,vec2(1.0)))) return 0.0;
  return smoothstep(.04,.99,texture2D(uCharacter,uv).a);
}
void main(){
  vec2 uv=uRegion.xy+vUv*uRegion.zw;
  vec3 bg=texture2D(uBackground,uv).rgb;
  vec2 cuv=animateFigure(characterUv(uv));
  vec4 person=texture2D(uCharacter,cuv);
  person.a=characterAlpha(cuv);
  // Foreground desk occludes the skirt along its perspective edge, not a horizontal cut.
  float deskTop=(629.0+.067*(uv.x*1536.0))/1024.0;
  person.a*=1.0-smoothstep(deskTop-1.0/1024.0,deskTop+1.0/1024.0,1.0-uv.y);
  vec3 map=texture2D(uNormal,cuv).rgb;
  vec3 n=normalize(map*2.0-1.0+vec3(0.0,0.0,0.0001));
  vec3 color=person.rgb;
  if(uMode==3){
    gl_FragColor=vec4(texture2D(uReference,uv).rgb,1.0);
  }else if(uMode==2){
    // Display data-space normal values without applying a second sRGB encoding.
    vec3 debugLinear=pow(clamp(map,0.0,1.0),vec3(2.2));
    float grey=dot(bg,vec3(.2126,.7152,.0722));
    gl_FragColor=vec4(mix(vec3(grey*.5),debugLinear,person.a),1.0);
  }else{
    if(uMode==0){
      bg=windowAtmosphere(flowingPaper(bg,uv),uv);
      vec3 cold=vec3(.66,.79,1.0);
      vec3 warm=vec3(1.0,.70,.41);
      vec3 warmDir=normalize(vec3(-.75,.1,.85));
      float diffuseCold=max(dot(n,uLight),0.0);
      float diffuseWarm=max(dot(n,warmDir),0.0);
      // Original paint is shaded already. This adds restrained illustrative relighting,
      // rather than pretending the image is a physically neutral albedo map.
      color*=vec3(.66)+cold*uCool*.47*diffuseCold+warm*uWarm*.47*diffuseWarm;
      vec2 stepUv=uPixel*2.0/.63;
      float aL=characterAlpha(cuv-vec2(stepUv.x,0));
      float aR=characterAlpha(cuv+vec2(stepUv.x,0));
      float aD=characterAlpha(cuv-vec2(0,stepUv.y));
      float aU=characterAlpha(cuv+vec2(0,stepUv.y));
      vec2 outward=normalize(vec2(aL-aR,aD-aU)+vec2(.00001));
      float edge=clamp(person.a-min(min(aL,aR),min(aD,aU)),0.0,1.0);
      float direction=max(dot(outward,normalize(uLight.xy)),0.0);
      float fresnel=pow(1.0-clamp(n.z,0.0,1.0),3.0);
      color+=cold*uRim*min(uCool,1.0)*(edge*direction*.36+fresnel*diffuseCold*.035);
      // Soft contact proxy anchored to the cushion, behind the character only.
      vec2 contact=(uv-vec2(.742,.474))/vec2(.070,.014);
      vec2 sleeveContact=(uv-vec2(.562,.489))/vec2(.034,.009);
      float shadow=max(exp(-dot(contact,contact)*1.8),.55*exp(-dot(sleeveContact,sleeveContact)*1.8));
      bg*=1.0-uShadow*shadow;
    }
    gl_FragColor=vec4(mix(bg,color,person.a),1.0);
  }
  #include <colorspace_fragment>
}`;

/** A fixed-view 2.5D composition. Never writes learning records or timer state. */
export async function mountAutumnScene(host, options={}) {
 let disposed=false,ready=false,visible=true,frame=0,last=0,time=0,frames=0,failed=false;
 let settings={focus:false,motion:true,intensity:'visible',...options};
 let renderer,geometry,material,resizeObserver,intersectionObserver,lastWidth=0,lastHeight=0;
 const textures=[];
 const reduced=matchMedia('(prefers-reduced-motion: reduce)');
 const region=new THREE.Vector4(0,0,1,1),target=new THREE.Vector4();
 const uniforms={uBackground:{value:null},uCharacter:{value:null},uNormal:{value:null},uReference:{value:null},
  uWeather:{value:1},uMotionGain:{value:1},uTime:{value:0},uBreath:{value:1},uSleeve:{value:1},uStars:{value:1},
  uMode:{value:0},uRegion:{value:region},uCool:{value:.85},uWarm:{value:.55},uRim:{value:.28},uShadow:{value:.24},
  uLight:{value:new THREE.Vector3(.574,.55,.819).normalize()},uPixel:{value:new THREE.Vector2(1/1536,1/1024)}};
 const scene=new THREE.Scene(),camera=new THREE.OrthographicCamera(-1,1,1,-1,0,1);
 const canMove=()=>settings.motion&&!reduced.matches;
 const movingCamera=()=>Math.abs(region.x-target.x)+Math.abs(region.y-target.y)+Math.abs(region.z-target.z)+Math.abs(region.w-target.w)>.0001;
 function setTarget(){
  const aspect=Math.max(.2,host.clientWidth/Math.max(1,host.clientHeight));
  // The scene and UVs share image aspect 3:2; preserve anatomy at all viewport sizes.
  let w,h,cx,cy;
  if(settings.focus){w=Math.min(.70,.60*aspect/1.5);h=w*1.5/aspect;cx=aspect<1?.70:.65;cy=.40+h/2;}
  else {w=Math.min(1,aspect/1.5);h=w*1.5/aspect;cx=.59;cy=.52;}
  target.set(Math.max(0,Math.min(1-w,cx-w/2)),Math.max(0,Math.min(1-h,cy-h/2)),w,h);
 }
 function render(){if(!ready||disposed||!visible||document.hidden)return;
  renderer.render(scene,camera);host.dataset.frames=String(++frames);host.dataset.sceneTime=time.toFixed(3);
 }
 function animate(stamp){
  frame=0;if(disposed||!ready||!visible||document.hidden)return;
  if(last&&stamp-last<1000/30){frame=requestAnimationFrame(animate);return;}
  const dt=last?Math.min(.1,(stamp-last)/1000):1/30;last=stamp;
  if(canMove())time+=dt;
  uniforms.uTime.value=time;
  if(canMove())region.lerp(target,1-Math.exp(-dt*4.5));else region.copy(target);
  render();if(canMove()||movingCamera())frame=requestAnimationFrame(animate);
 }
 function wake(){cancelAnimationFrame(frame);frame=0;last=0;
  if(!ready||disposed)return;
  uniforms.uMotionGain.value=settings.intensity==='gentle'?.45:1;
  uniforms.uBreath.value=reduced.matches?0:1;uniforms.uSleeve.value=reduced.matches?0:1;
  if(!canMove())region.copy(target);
  render();if(visible&&!document.hidden&&(canMove()||movingCamera()))frame=requestAnimationFrame(animate);
 }
 function resize(){if(!renderer||disposed)return;
  const width=host.clientWidth,height=host.clientHeight;if(width===lastWidth&&height===lastHeight)return;lastWidth=width;lastHeight=height;setTarget();
  renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.5));
  renderer.setSize(Math.max(1,host.clientWidth),Math.max(1,host.clientHeight),false);
  // Refit immediately on viewport changes to avoid a transient stretch of the figure.
  region.copy(target);wake();
 }
 function lost(e){e.preventDefault();options.onState?.('fallback');dispose();}
 function dispose(){if(disposed)return;disposed=true;cancelAnimationFrame(frame);
  resizeObserver?.disconnect();intersectionObserver?.disconnect();
  document.removeEventListener('visibilitychange',wake);reduced.removeEventListener('change',wake);
  renderer?.domElement.removeEventListener('webglcontextlost',lost);
  textures.forEach(t=>t.dispose());material?.dispose();geometry?.dispose();renderer?.dispose();
  renderer?.domElement.remove();options.signal?.removeEventListener('abort',dispose);host.dataset.running='false';
 }
 const control={set(next){settings={...settings,...next};setTarget();wake();},dispose};
 options.signal?.addEventListener('abort',dispose,{once:true});
 if(options.signal?.aborted){dispose();return control;}
 try{
  renderer=new THREE.WebGLRenderer({antialias:false,alpha:false,powerPreference:'low-power'});
  renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.NoToneMapping;
  renderer.debug.onShaderError=()=>{failed=true;options.onState?.('fallback');queueMicrotask(dispose);};
  renderer.domElement.setAttribute('aria-hidden','true');host.appendChild(renderer.domElement);
  renderer.domElement.addEventListener('webglcontextlost',lost);
  const loader=new THREE.TextureLoader();
  await Promise.all([['uBackground','autumn-room.png'],['uCharacter','scholar-cutout.png'],['uNormal','scholar-normal.png']].map(async([name,file])=>{
   const texture=await loader.loadAsync('/autumn/'+file);
   if(disposed){texture.dispose();return;}
   texture.colorSpace=name==='uNormal'?THREE.NoColorSpace:THREE.SRGBColorSpace;
   texture.minFilter=THREE.LinearFilter;texture.magFilter=THREE.LinearFilter;texture.generateMipmaps=false;
   textures.push(texture);uniforms[name].value=texture;
  }));
  if(disposed)return control;
  // The production renderer has no comparison mode, and needs no fourth GPU texture.
  uniforms.uReference.value=uniforms.uBackground.value;
  geometry=new THREE.PlaneGeometry(2,2);material=new THREE.ShaderMaterial({uniforms,fragmentShader,
   vertexShader:'varying vec2 vUv;void main(){vUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}'});
  scene.add(new THREE.Mesh(geometry,material));
  ready=true;resize();if(failed)return control;
  if(settings.focus&&canMove()){const destination=target.clone();settings.focus=false;setTarget();region.copy(target);settings.focus=true;target.copy(destination);}
  resizeObserver=new ResizeObserver(resize);resizeObserver.observe(host);
  intersectionObserver=new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;wake();});intersectionObserver.observe(host);
  document.addEventListener('visibilitychange',wake);reduced.addEventListener('change',wake);
  options.onState?.('ready');wake();return control;
 }catch(error){dispose();options.onState?.('fallback');return control;}
}
