import React,{useEffect,useRef,useState} from 'react';
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';

export default function DetectiveDesk({theme,onAction,onUnavailable}){
 const host=useRef(),reset=useRef(),actionRef=useRef(onAction),[hover,setHover]=useState('');actionRef.current=onAction;
 useEffect(()=>{
  const element=host.current;if(!element)return;
  let renderer,controls,observer,frame=0,visible=true,disposed=false;
  const scene=new THREE.Scene(),dark=theme==='noir';
  const palette={wood:dark?0x252a2e:0xa88760,paper:dark?0xcbbda5:0xf2e3c7,folder:dark?0x846c49:0xbd9a6b,metal:0xb59a63,ink:0x443c32,board:dark?0x4a4238:0x9c8464};
  const camera=new THREE.PerspectiveCamera(32,1,.1,70);camera.position.set(9,10,12);
  const materials=[],geometries=[];
  const mat=(color,roughness=.8,metalness=0)=>{const m=new THREE.MeshStandardMaterial({color,roughness,metalness});materials.push(m);return m};
  const wood=mat(palette.wood),paper=mat(palette.paper),folder=mat(palette.folder),brass=mat(palette.metal,.38,.6),ink=mat(palette.ink),boardmat=mat(palette.board),red=mat(dark?0x925749:0x8a3f35);
  function mesh(g,m,parent,x=0,y=0,z=0){geometries.push(g);const o=new THREE.Mesh(g,m);o.position.set(x,y,z);o.castShadow=true;o.receiveShadow=true;parent.add(o);return o}
  function box(w,h,d,m,parent,x=0,y=0,z=0){return mesh(new THREE.BoxGeometry(w,h,d),m,parent,x,y,z)}
  function cylinder(rt,rb,h,m,parent,x,y,z){return mesh(new THREE.CylinderGeometry(rt,rb,h,32),m,parent,x,y,z)}
  function group(action,label,x,y,z){const g=new THREE.Group();g.position.set(x,y,z);g.userData={action,label};scene.add(g);return g}
  // Desk and bevel-like edge; all assets are locally constructed geometry.
  box(10,.38,6.5,wood,scene,0,-.3,0);box(9.9,.08,6.4,brass,scene,0,-.52,0);
  const leather=mat(dark?0x263634:0x657163);box(4.9,.035,3.3,leather,scene,-.1,-.08,.9);
  const archive=group('continue','当前档案 · 继续学习',-.2,.02,1.1);archive.rotation.y=-.1;
  box(2.9,.11,2.05,folder,archive);box(.9,.04,.3,folder,archive,-.8,.04,-1.1);
  for(let i=0;i<4;i++)box(2.55,.025,1.8,paper,archive,.03,.09+i*.026,0);
  const cover=box(2.9,.035,2.05,folder,archive,0,.22,0);cover.rotation.z=-.018;
  box(1.2,.008,.54,paper,archive,.1,.25,-.25);
  for(let i=0;i<3;i++)box(.85-i*.17,.009,.018,ink,archive,.03,.26,-.39+i*.13);
  const stamp=mesh(new THREE.RingGeometry(.21,.23,32),red,archive,.85,.265,.55);stamp.rotation.x=-Math.PI/2;
  const books=group('library','教程与资料库',-3.5,.01,-.3);books.rotation.y=.16;
  [0x645a4b,0x6e443c,0x4d5c52].forEach((color,i)=>{const m=mat(color);box(1.65,.33,2.2,m,books,0,.18+i*.4,0);box(1.57,.24,2.08,paper,books,.03,.18+i*.4,.04);box(1.67,.025,2.22,m,books,0,.35+i*.4,0)});
  const board=group('graph','证据板 · 知识图谱',-.8,1.5,-2.3);board.rotation.x=-.15;
  box(4.6,2.7,.14,ink,board);box(4.4,2.5,.16,boardmat,board,0,0,.03);
  const points=[[-1.4,.65],[-.1,.55],[1.25,.55],[-.85,-.65],[.8,-.65]];
  points.forEach(([x,y],i)=>{const card=box(.77,.65,.02,paper,board,x,y,.14);card.rotation.z=(i%2?.08:-.08);for(let j=0;j<3;j++)box(.47-j*.08,.018,.012,ink,board,x,y+.15-j*.12,.16);mesh(new THREE.SphereGeometry(.047,12,12),red,board,x,y+.23,.19)});
  [[0,1],[1,2],[0,3],[1,4],[4,2]].forEach(([a,b])=>{const p1=new THREE.Vector3(points[a][0],points[a][1]+.23,.18),p2=new THREE.Vector3(points[b][0],points[b][1]+.23,.18);const curve=new THREE.LineCurve3(p1,p2);mesh(new THREE.TubeGeometry(curve,1,.009,4,false),red,board)});
  const notebook=group('reviews','笔记本 · 错题与复习',2.6,.04,1.8);notebook.rotation.y=-.16;
  box(1.5,.2,1.95,ink,notebook);box(1.42,.14,1.88,paper,notebook,0,.03,0);box(1.5,.02,1.95,ink,notebook,0,.12,0);box(.025,.01,1.97,brass,notebook,.45,.14,0);
  const pen=cylinder(.035,.035,1.65,brass,notebook,-.5,.2,.1);pen.rotation.x=Math.PI/2;pen.rotation.z=.1;
  const lamp=group('focus','台灯 · 专注模式',3.4,.05,-1.4);
  cylinder(.52,.62,.12,brass,lamp,0,0,0);cylinder(.07,.09,2.1,brass,lamp,0,1.1,0);
  const shade=cylinder(.45,.85,.6,mat(dark?0x31443f:0x506651,.5,.2),lamp,-.18,2.2,0);shade.rotation.z=.16;
  const light=new THREE.SpotLight(0xffd9a3,dark?40:18,14,.75,.7,1.3);light.position.set(3.1,2.1,-1.3);light.target.position.set(1,0,1);light.castShadow=true;light.shadow.mapSize.set(1024,1024);scene.add(light,light.target);
  const ambient=new THREE.HemisphereLight(dark?0x939faa:0xffffff,dark?0x24231f:0x7e6a50,dark?1.8:2.5);scene.add(ambient);
  const sun=new THREE.DirectionalLight(0xffebd4,dark?1.3:3);sun.position.set(-3,8,4);sun.castShadow=true;sun.shadow.mapSize.set(1024,1024);sun.shadow.camera.left=-7;sun.shadow.camera.right=7;sun.shadow.camera.top=7;sun.shadow.camera.bottom=-7;sun.shadow.normalBias=.04;scene.add(sun);
  scene.background=new THREE.Color(dark?0x181d21:0xe9decb);
  const ray=new THREE.Raycaster(),pointer=new THREE.Vector2();let down=null;
  function draw(){if(!disposed&&visible&&!document.hidden&&renderer)renderer.render(scene,camera)}
  function resize(){if(!renderer)return;const w=element.clientWidth,h=element.clientHeight;renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();draw()}
  function hit(e){const r=element.getBoundingClientRect();pointer.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);ray.setFromCamera(pointer,camera);let o=ray.intersectObjects(scene.children,true)[0]?.object;while(o&&!o.userData.action)o=o.parent;return o}
  function move(e){const o=hit(e);setHover(o?.userData.label||'');element.style.cursor=o?'pointer':'grab'}
  function up(e){if(down&&Math.hypot(e.clientX-down.x,e.clientY-down.y)<5){const o=hit(e);if(o)actionRef.current(o.userData.action)}down=null}
  const press=e=>{down={x:e.clientX,y:e.clientY}},leave=()=>setHover('');
  try{
   renderer=new THREE.WebGLRenderer({antialias:true,powerPreference:'low-power'});renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFShadowMap;renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=dark?1.2:1;
   renderer.domElement.addEventListener('webglcontextlost',onUnavailable,{once:true});renderer.domElement.setAttribute('aria-hidden','true');element.appendChild(renderer.domElement);
   controls=new OrbitControls(camera,renderer.domElement);controls.target.set(0,.3,0);controls.enablePan=false;controls.enableZoom=false;controls.minPolarAngle=.5;controls.maxPolarAngle=1.05;controls.minAzimuthAngle=.2;controls.maxAzimuthAngle=1;controls.update();controls.saveState();controls.addEventListener('change',draw);reset.current=()=>{controls.reset();draw()};
   observer=new ResizeObserver(resize);observer.observe(element);resize();
   element.addEventListener('pointermove',move);element.addEventListener('pointerdown',press);element.addEventListener('pointerup',up);element.addEventListener('pointerleave',leave);document.addEventListener('visibilitychange',draw);
   const intersection=new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;if(visible)draw()});intersection.observe(element);
   return()=>{disposed=true;intersection.disconnect();observer.disconnect();controls.dispose();renderer.domElement.removeEventListener('webglcontextlost',onUnavailable);light.shadow.dispose();sun.shadow.dispose();renderer.dispose();renderer.forceContextLoss();renderer.domElement.remove();materials.forEach(m=>m.dispose());geometries.forEach(g=>g.dispose());element.removeEventListener('pointermove',move);element.removeEventListener('pointerdown',press);element.removeEventListener('pointerup',up);element.removeEventListener('pointerleave',leave);document.removeEventListener('visibilitychange',draw);cancelAnimationFrame(frame)};
  }catch(e){renderer?.dispose();materials.forEach(m=>m.dispose());geometries.forEach(g=>g.dispose());onUnavailable()}
 },[theme]);
 return <div className="desk-live"><div className="desk-canvas" ref={host}/><div className="desk-instructions"><span>{hover||'拖动观察 · 点击物件开始学习'}</span><button onClick={()=>reset.current?.()}>重置视角</button></div></div>
}
