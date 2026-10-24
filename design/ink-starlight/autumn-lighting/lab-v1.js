import * as THREE from './vendor/three.module.js';

const $ = (selector) => document.querySelector(selector);
const stage = $('#stage');
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const defaults = { cool: .85, angle: -35, warm: .55, rim: .28, shadow: .24 };
const state = { ...defaults, mode: 'lit', crop: 'full', visible: true, ready: false, disposed: false };
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
  vec2 cuv=characterUv(uv);
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
function animate(time) {
  frame = 0;
  if (!shouldAnimate()) return;
  uniforms.uWarm.value = state.warm * (1 + .018 * Math.sin(time * .0017) + .009 * Math.sin(time * .0031));
  render();
  frame = requestAnimationFrame(animate);
}
function shouldAnimate() {
  return state.ready && !state.disposed && state.visible && !document.hidden && !reducedMotion.matches && $('#flicker').checked && state.mode === 'lit';
}
function motionChanged() {
  cancelAnimationFrame(frame);
  frame = 0;
  uniforms.uWarm.value = state.warm;
  render();
  if (shouldAnimate()) frame = requestAnimationFrame(animate);
  $('#flicker').disabled = reducedMotion.matches || !state.ready || state.mode !== 'lit';
  $('#flicker').closest('label').title = reducedMotion.matches ? '系统已开启减少动态效果' : '';
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
  cancelAnimationFrame(frame);
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
  cancelAnimationFrame(frame);
  resizeObserver?.disconnect(); visibilityObserver?.disconnect();
  reducedMotion.removeEventListener('change', motionChanged);
  document.removeEventListener('visibilitychange', motionChanged);
  textures.forEach(t => t.dispose());
  material?.dispose(); geometry?.dispose(); renderer?.dispose();
}

for (const name of Object.keys(defaults)) $('#' + name).addEventListener('input', sync);
$('#flicker').addEventListener('change', motionChanged);
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
  } catch (error) { fail(error); }
}
window.addEventListener('pagehide', dispose, {once: true});
window.addEventListener('pageshow', event => {if (event.persisted) location.reload();});
initialize();
