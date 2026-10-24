import * as THREE from './vendor/three.module.js';
import {motionShader} from './scene-shader.js?v=8';
import {createFocus} from './focus.js?v=5';

const $ = (selector) => document.querySelector(selector);
const stage = $('#stage');
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
let savedMotion={};
try{savedMotion=JSON.parse(localStorage.getItem('learnloop.autumn.motion.v1'))||{};}catch{}
for(const id of ['breathing','sleeve','stars','weather'])if(typeof savedMotion[id]==='boolean')$('#'+id).checked=savedMotion[id];
function saveMotion(){try{localStorage.setItem('learnloop.autumn.motion.v1',JSON.stringify({paused:state.paused,intensity:$('#motion-intensity').value,...Object.fromEntries(['breathing','sleeve','stars','weather'].map(id=>[id,$('#'+id).checked]))}));}catch{}}
$('#motion-intensity').value=savedMotion.intensity==='gentle'?'gentle':'visible';
const defaults = { cool: .85, angle: -35, warm: .55, rim: .28, shadow: .24 };
const state = { ...defaults, mode: 'lit', crop: 'full', visible: true, ready: false, disposed: false, paused: savedMotion.paused===true };
let focus, ambientTime=0, lastFrameTime=0;
const modes = { lit: 0, base: 1, normal: 2, reference: 3 };
const descriptions = {
  lit: '实时调整人物法线漫反射、方向性轮廓光和接触阴影。',
  base: '原始人物颜色合成：保留画稿原有明暗，关闭新增灯光和接触阴影。',
  normal: '近似法线检查：RGB 表示朝向；绘制法线不是三维模型烘焙数据。',
  reference: '生图全景原稿，用于对照人物位置与整体氛围；此模式不受灯光滑杆影响。'
};
// Regions are in bottom-left-origin image UVs. Same 3:2 aspect as the canvas.
const crops = {
  full: [0, 0, 1, 1],
  figure: [.47, .38, .46, .46],
  contact: [.50, .34, .32, .32]
};
let renderer, material, scene, camera, geometry, resizeObserver, visibilityObserver;
let textures = [], frame = 0, renderCount = 0;
const uniforms = {
  uBackground: {value: null}, uCharacter: {value: null}, uNormal: {value: null}, uReference: {value: null},
  uWeather: {value: 1}, uMotionGain: {value: 1}, uTime: {value: 0}, uBreath: {value: 1}, uSleeve: {value: 1}, uStars: {value: 1},
  uMode: {value: 0}, uRegion: {value: new THREE.Vector4(...crops.full)},
  uCool: {value: .85}, uWarm: {value: .55}, uRim: {value: .28}, uShadow: {value: .24},
  uLight: {value: new THREE.Vector3(.6, .55, .8).normalize()},
  uPixel: {value: new THREE.Vector2(1 / 1536, 1 / 1024)}
};
const vertexShader = `varying vec2 vUv;
void main(){vUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}`;
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

function render() {
  if (!state.ready || state.disposed || !state.visible || document.hidden) return;
  renderer.render(scene, camera);
  renderCount++;
  stage.dataset.frames = String(renderCount);
  stage.dataset.mode = state.mode;
  stage.dataset.crop = state.crop;
  stage.dataset.sceneTime = ambientTime.toFixed(3);
  $('#render-state').textContent = `WebGL · ${renderer.info.render.calls} draw · ${renderer.info.memory.textures} textures`;
}
function resize() {
  if (!renderer || state.disposed) return;
  const width = stage.clientWidth, height = stage.clientHeight;
  if (!width || !height) return;
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.5));
  renderer.setSize(width, height, false);
  render();
}
function sync() {
  for (const name of Object.keys(defaults)) {
    state[name] = Number($('#' + name).value);
    $('#' + name + '-value').textContent = name === 'angle' ? state[name] + '°' : state[name].toFixed(2);
  }
  uniforms.uCool.value = state.cool;
  uniforms.uWarm.value = state.warm;
  uniforms.uRim.value = state.rim;
  uniforms.uShadow.value = state.shadow;
  const angle = THREE.MathUtils.degToRad(state.angle);
  uniforms.uLight.value.set(-Math.sin(angle), .55, Math.cos(angle)).normalize();
  render();
}
function ambientAllowed(){return !reducedMotion.matches && !state.paused;}
function ambientEnabled(){return state.ready && state.mode==='lit' && ambientAllowed() && ['breathing','sleeve','stars','weather','flicker'].some(id=>$('#'+id).checked);}
function animate(time) {
  frame=0;
  if(!shouldAnimate())return;
  // Continuous ambient effects are capped at 30 Hz; the brief camera tween uses display cadence.
  if(!focus?.isTransitioning() && lastFrameTime && time-lastFrameTime<1000/30){frame=requestAnimationFrame(animate);return;}
  const dt=lastFrameTime?Math.min(.1,(time-lastFrameTime)/1000):0;
  lastFrameTime=time;
  if(ambientEnabled())ambientTime+=dt;
  uniforms.uTime.value=ambientTime;
  uniforms.uWarm.value=state.warm*(ambientEnabled() && $('#flicker').checked ? 1+.018*Math.sin(ambientTime*1.7)+.009*Math.sin(ambientTime*3.1):1);
  focus?.tick(time);
  render();
  if(shouldAnimate())frame=requestAnimationFrame(animate);
}
function shouldAnimate(){
  return !state.disposed && !document.hidden && (state.visible || focus?.isActive()) && (focus?.isTransitioning() || ambientEnabled());
}
function motionChanged(){
  cancelAnimationFrame(frame);frame=0;lastFrameTime=0;
  const allowed=ambientAllowed();
  uniforms.uBreath.value=$('#breathing').checked && state.mode==='lit'?1:0;
  uniforms.uSleeve.value=$('#sleeve').checked && state.mode==='lit'?1:0;
  uniforms.uStars.value=$('#stars').checked && state.mode==='lit'?1:0;
  uniforms.uWeather.value=$('#weather').checked && state.mode==='lit'?1:0;
  uniforms.uMotionGain.value=$('#motion-intensity').value==='gentle'?.45:1;
  // Freeze the current pose on pause. A reduced-motion preference starts at the still pose.
  if(reducedMotion.matches){uniforms.uBreath.value=0;uniforms.uSleeve.value=0;focus?.settle();}
  uniforms.uWarm.value=state.warm;
  $('#pause-motion').setAttribute('aria-pressed',String(state.paused));
  $('#pause-motion').textContent=state.paused?'恢复动效':'暂停动效';
  $('#focus-pause').setAttribute('aria-pressed',String(state.paused));
  $('#focus-pause').textContent=state.paused?'恢复动效':'暂停动效';
  $('#motion-note').textContent=reducedMotion.matches?'系统已开启减少动态效果：人物保持静止，镜头直接切换。':state.paused?'动效已暂停，番茄钟仍按实际时间计时。':'';
  updateMotionStatus();
  for(const id of ['breathing','sleeve','stars','weather','flicker'])$('#'+id).disabled=reducedMotion.matches || !state.ready || state.mode!=='lit';
  if(!allowed)focus?.settle();
  render();
  if(shouldAnimate())frame=requestAnimationFrame(animate);
}
function updateMotionStatus(){
  const active=['breathing','sleeve','weather','stars','flicker'].filter(id=>$('#'+id).checked);
  const labels={breathing:'呼吸',sleeve:'衣袖',weather:'落叶与星光',stars:'案头星轨',flicker:'烛光'};
  const status=!state.ready?'静态预览 · 场景动效不可用':reducedMotion.matches?'静止 · 遵循系统减少动态效果':state.paused?'动效已暂停 · 计时不受影响':!active.length?'静止 · 请返回全景开启动效':'动效中 · '+active.filter(id=>id!=='stars').map(id=>labels[id]).join(' / ');
  $('#focus-motion-status').textContent=active.length===1 && active[0]==='stars' && state.ready && ambientAllowed()?'仅案头星轨开启 · 专注视角不可见':status;
  $('#focus-motion-status').dataset.running=String(ambientEnabled());
  $('#motion-intensity').disabled=!state.ready || reducedMotion.matches;
  $('#focus-pause').disabled=!state.ready || reducedMotion.matches;
}
function selectMode(mode) {
  state.mode = mode;
  uniforms.uMode.value = modes[mode];
  document.querySelectorAll('[data-mode]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.mode === mode)));
  $('#mode-description').textContent = descriptions[mode];
  document.querySelectorAll('.controls input,.presets button').forEach(el => {el.disabled = !state.ready || mode !== 'lit';});
  motionChanged();
  if (mode !== 'lit') $('#flicker').disabled = true;
}
function fail(error) {
  state.ready = false;
  updateMotionStatus();
  cancelAnimationFrame(frame);
  for(const id of ['breathing','sleeve','stars','weather','pause-motion'])$('#'+id).disabled=true;
  focus?.settle();
  $('#canvas-host').hidden = true;
  $('#status').textContent = '受光预览不可用，已保留全景静态稿。可重新加载或直接查看下方素材。';
  $('#retry').hidden = false;
  $('#render-state').textContent = '静态回退';
  $('#mode-description').textContent = '当前显示全景静态稿，调光暂不可用。';
  stage.dataset.status = 'fallback';
  document.querySelectorAll('.controls input,.controls button,[data-mode],[data-crop]').forEach(el => {el.disabled = true;});
  console.error('Autumn lighting preview:', error?.message || 'WebGL unavailable');
}
function dispose() {
  state.disposed = true;
  focus?.dispose();
  cancelAnimationFrame(frame);
  resizeObserver?.disconnect(); visibilityObserver?.disconnect();
  reducedMotion.removeEventListener('change', motionChanged);
  document.removeEventListener('visibilitychange', motionChanged);
  textures.forEach(t => t.dispose());
  material?.dispose(); geometry?.dispose(); renderer?.dispose();
}

for (const name of Object.keys(defaults)) $('#' + name).addEventListener('input', sync);
for(const id of ['breathing','sleeve','stars','weather','flicker'])$('#'+id).addEventListener('change',()=>{saveMotion();motionChanged();});
for(const id of ['pause-motion','focus-pause'])$('#'+id).addEventListener('click',()=>{state.paused=!state.paused;saveMotion();motionChanged();});
$('#motion-intensity').addEventListener('change',()=>{saveMotion();motionChanged();});
$('#retry').addEventListener('click', () => location.reload());
document.querySelectorAll('[data-mode]').forEach(b => b.addEventListener('click', () => selectMode(b.dataset.mode)));
document.querySelectorAll('[data-crop]').forEach(b => b.addEventListener('click', () => {
  state.crop = b.dataset.crop;
  uniforms.uRegion.value.set(...crops[state.crop]);
  document.querySelectorAll('[data-crop]').forEach(el => el.setAttribute('aria-pressed', String(el === b)));
  render();
}));
document.querySelectorAll('[data-preset]').forEach(b => b.addEventListener('click', () => {
  const values = b.dataset.preset === 'moon' ? {...defaults, cool: 1.4, warm: 0} : b.dataset.preset === 'candle' ? {...defaults, cool: 0, warm: 1.4} : defaults;
  Object.entries(values).forEach(([name, value]) => {$('#' + name).value = value;});
  $('#flicker').checked = false;
  sync(); motionChanged();
}));

focus=createFocus({stage,beforeEnter:()=>selectMode('lit'),getRegion:()=>uniforms.uRegion.value.toArray(),setRegion:r=>uniforms.uRegion.value.set(...r),wake:motionChanged,allowMotion:()=>ambientAllowed() && state.ready});

async function initialize() {
  try {
    renderer = new THREE.WebGLRenderer({ antialias: false, alpha: false, powerPreference: 'low-power' });
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.NoToneMapping;
    renderer.domElement.setAttribute('aria-label', '秋夜书斋人物受光合成，使用下方控件调整');
    renderer.domElement.setAttribute('role', 'img');
    renderer.domElement.addEventListener('webglcontextlost', event => { event.preventDefault(); fail(new Error('WebGL context lost')); });
    $('#canvas-host').append(renderer.domElement);
    scene = new THREE.Scene(); camera = new THREE.OrthographicCamera(-1, 1, 1, -1, .1, 5); camera.position.z = 1;
    const loader = new THREE.TextureLoader();
    const paths = ['autumn-room.png', 'scholar-cutout.png', 'scholar-normal.png', 'autumn-master.png'];
    const results = await Promise.allSettled(paths.map(async (path, i) => {
      const texture = await loader.loadAsync('assets/' + path);
      textures.push(texture);
      texture.colorSpace = i === 2 ? THREE.NoColorSpace : THREE.SRGBColorSpace;
      texture.minFilter = THREE.LinearFilter; texture.magFilter = THREE.LinearFilter;
      texture.generateMipmaps = false;
      return texture;
    }));
    if (results.some(result => result.status === 'rejected')) throw new Error('One or more local image assets could not be loaded');
    ['uBackground', 'uCharacter', 'uNormal', 'uReference'].forEach((key, i) => {uniforms[key].value = results[i].value;});
    if (state.disposed) { textures.forEach(t => t.dispose()); return; }
    geometry = new THREE.PlaneGeometry(2, 2);
    material = new THREE.ShaderMaterial({ uniforms, vertexShader, fragmentShader, depthTest: false, depthWrite: false });
    scene.add(new THREE.Mesh(geometry, material));
    renderer.debug.onShaderError = () => fail(new Error('Lighting shader compilation failed'));
    state.ready = true;
    stage.dataset.status = 'ready';
    $('#status').textContent = '';
    resize(); sync(); selectMode('lit');
    resizeObserver = new ResizeObserver(resize); resizeObserver.observe(stage);
    visibilityObserver = new IntersectionObserver(([entry]) => {state.visible = entry.isIntersecting; motionChanged();});
    visibilityObserver.observe(stage);
    reducedMotion.addEventListener('change', motionChanged);
    document.addEventListener('visibilitychange', motionChanged);
    focus.restoreView();
  } catch (error) { fail(error); }
}
window.addEventListener('pagehide', dispose, {once: true});
window.addEventListener('pageshow', event => {if (event.persisted) location.reload();});
initialize();
