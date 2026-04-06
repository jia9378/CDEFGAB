/* ==========================================================================
   CDEFGAB — Hero Three.js Scene
   ========================================================================== */

const container = document.getElementById('canvas-container');

/* ---- Renderer ---- */
const renderer = new THREE.WebGLRenderer({
  antialias: true,
  alpha: true,
  powerPreference: 'high-performance',
});
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.outputEncoding = THREE.sRGBEncoding;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.8;
renderer.shadowMap.enabled = false;
container.appendChild(renderer.domElement);

/* ---- Scene ---- */
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x080604);
scene.fog = new THREE.Fog(0x080604, 8, 25);

/* ---- Camera ---- */
const camera = new THREE.PerspectiveCamera(
  40,
  window.innerWidth / window.innerHeight,
  0.1,
  100
);
camera.position.set(3, 2, 4);
camera.lookAt(0, 0, 0);

/* ---- Controls (drag, zoom, rotate all enabled) ---- */
const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.05;
controls.enableZoom = false;
controls.enablePan = true;
controls.enableRotate = true;
controls.autoRotate = true;
controls.autoRotateSpeed = 0.3;
controls.minPolarAngle = Math.PI * 0.05;
controls.maxPolarAngle = Math.PI * 0.85;
controls.minDistance = 1;
controls.maxDistance = 15;
controls.target.set(0, 0.5, 0);

/*
   Camera position logger:
   Every time you release the mouse after dragging/zooming,
   the current camera position and target are printed to the
   browser console (F12 → Console tab).

   1. Drag/zoom to the angle you want
   2. Open DevTools console
   3. Copy the logged numbers into the VIEWS object below
*/
controls.addEventListener('end', function () {
  console.log(
    'pos: { x:',  camera.position.x.toFixed(2) + ',',
    'y:',          camera.position.y.toFixed(2) + ',',
    'z:',          camera.position.z.toFixed(2),
    '}'
  );
  console.log(
    'target: { x:', controls.target.x.toFixed(2) + ',',
    'y:',           controls.target.y.toFixed(2) + ',',
    'z:',           controls.target.z.toFixed(2),
    '}'
  );
});


/* ---- Lighting ---- */

const keyLight = new THREE.DirectionalLight(0xFFD4A0, 1.2);
keyLight.position.set(-3, 6, 2);
scene.add(keyLight);

const fillLight = new THREE.DirectionalLight(0xA0C4FF, 0.3);
fillLight.position.set(4, 3, -2);
scene.add(fillLight);

const innerGlow = new THREE.PointLight(0xD4956A, 0.8, 8);
innerGlow.position.set(0, 1.5, 0);
scene.add(innerGlow);

const ambient = new THREE.AmbientLight(0x2A1A0A, 0.4);
scene.add(ambient);

const rimLight = new THREE.DirectionalLight(0xFFE0C0, 0.4);
rimLight.position.set(0, 2, -5);
scene.add(rimLight);

// Spotlight on the keys — from right side, angled down
const keySpot = new THREE.SpotLight(0xFFE0C0, 3.0);
keySpot.position.set(4, 4, 1);
keySpot.target.position.set(0, 0, 0);
keySpot.angle = Math.PI / 5;       // cone width
keySpot.penumbra = 0.6;            // soft edge
keySpot.distance = 15;
keySpot.decay = 1.5;
scene.add(keySpot);
scene.add(keySpot.target);


/* ---- Load GLB Model ---- */

const MODEL_PATH = 'grand_piano.glb';
const gltfLoader = new THREE.GLTFLoader();

gltfLoader.load(
  MODEL_PATH,

  function onLoad(gltf) {
    var model = gltf.scene;

    var box = new THREE.Box3().setFromObject(model);
    var center = box.getCenter(new THREE.Vector3());
    var size = box.getSize(new THREE.Vector3());
    var maxDim = Math.max(size.x, size.y, size.z);
    var scale = 4 / maxDim;

    model.scale.setScalar(scale);
    model.position.sub(center.multiplyScalar(scale));
    model.position.y -= size.y * scale * 0.15;
    model.position.x += 0.5;

    model.traverse(function (child) {
      if (child.isMesh) {
        if (!child.material.map) {
          child.material = new THREE.MeshStandardMaterial({
            color: 0x0A0705,
            metalness: 0.15,
            roughness: 0.4,
          });
        }
        child.material.envMapIntensity = 0.5;
      }
    });

    scene.add(model);
    document.getElementById('loader').classList.add('done');
  },

  function onProgress(progress) {
    if (progress.total > 0) {
      var pct = Math.round((progress.loaded / progress.total) * 100);
      document.querySelector('.loader-text').textContent = 'LOADING ' + pct + '%';
    }
  },

  function onError(error) {
    console.error('Model load error:', error);
    document.querySelector('.loader-text').textContent = 'MODEL NOT FOUND';
    setTimeout(function () {
      document.getElementById('loader').classList.add('done');
    }, 2000);
  }
);


/* ---- Scroll-linked fade ---- */

window.addEventListener('scroll', function () {
  var scrollY = window.scrollY;
  var heroH = window.innerHeight;
  var progress = Math.min(1, scrollY / (heroH * 0.6));

  container.style.opacity = 1 - progress;
  controls.autoRotateSpeed = 0.3 * (1 - progress);
});


/* ---- Resize ---- */

window.addEventListener('resize', function () {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});


/* ---- Render loop ---- */

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

animate();


/* ==================================================================
   Camera View Toggle
   
   Two preset views: overview (default) and strings (close-up).
   
   HOW TO FIND YOUR IDEAL ANGLES:
   1. Drag/zoom the model to the angle you want
   2. Open browser DevTools console (F12)
   3. You'll see "pos: { x: ..., y: ..., z: ... }" logged
   4. Copy those numbers into the VIEWS object below
   ================================================================== */

var currentView = 'overview';
var isAnimating = false;

var VIEWS = {
  overview: {
    pos:    { x: 3, y: 2, z: 4 },
    target: { x: 0, y: 0.5, z: 0 },
    label:  'Practice Anywhere',
  },
  strings: {
    pos: { x: 2.97, y: 1.03, z: -0.01 },
    target: { x: 1.20, y: -0.22, z: -0.01 },
    label:  'Back to Overview',
  },
};

function easeInOutCubic(t) {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

function toggleView() {
  if (isAnimating) return;

  var nextKey = currentView === 'overview' ? 'strings' : 'overview';
  var to = VIEWS[nextKey];

  isAnimating = true;
  controls.autoRotate = false;

  var startPos = {
    x: camera.position.x,
    y: camera.position.y,
    z: camera.position.z,
  };
  var startTarget = {
    x: controls.target.x,
    y: controls.target.y,
    z: controls.target.z,
  };

  var duration = 1800;
  var startTime = performance.now();

// Lighting presets per view
  var LIGHTS = {
  overview: {
    innerIntensity: 2.0,
    innerY: 1.0,
    keyIntensity: 2.5,
    ambientIntensity: 1.2,
    exposure: 1.4,
    spotIntensity: 3.0,
    spotTargetY: 0,
  },
  strings: {
    innerIntensity: 4.0,
    innerY: 0.6,
    keyIntensity: 1.5,
    ambientIntensity: 2.0,
    exposure: 1.8,
    spotIntensity: 5.0,
    spotTargetY: -3,
  },
};

  var fromLights = LIGHTS[currentView];
  var toLights = LIGHTS[nextKey];

  function animateCamera(now) {
    var elapsed = now - startTime;
    var progress = Math.min(1, elapsed / duration);
    var t = easeInOutCubic(progress);

    // Camera position
    camera.position.x = startPos.x + (to.pos.x - startPos.x) * t;
    camera.position.y = startPos.y + (to.pos.y - startPos.y) * t;
    camera.position.z = startPos.z + (to.pos.z - startPos.z) * t;

    // Camera target
    controls.target.x = startTarget.x + (to.target.x - startTarget.x) * t;
    controls.target.y = startTarget.y + (to.target.y - startTarget.y) * t;
    controls.target.z = startTarget.z + (to.target.z - startTarget.z) * t;

    // Lighting transition
    innerGlow.intensity = fromLights.innerIntensity + (toLights.innerIntensity - fromLights.innerIntensity) * t;
    innerGlow.position.y = fromLights.innerY + (toLights.innerY - fromLights.innerY) * t;
    keyLight.intensity = fromLights.keyIntensity + (toLights.keyIntensity - fromLights.keyIntensity) * t;
    keySpot.intensity = fromLights.spotIntensity + (toLights.spotIntensity - fromLights.spotIntensity) * t;
    keySpot.target.position.y = fromLights.spotTargetY + (toLights.spotTargetY - fromLights.spotTargetY) * t;
    ambient.intensity = fromLights.ambientIntensity + (toLights.ambientIntensity - fromLights.ambientIntensity) * t;
    renderer.toneMappingExposure = fromLights.exposure + (toLights.exposure - fromLights.exposure) * t;

    if (progress < 1) {
      requestAnimationFrame(animateCamera);
    } else {
      currentView = nextKey;
      isAnimating = false;
      document.getElementById('viewBtn').textContent = to.label;

      if (nextKey === 'overview') {
        controls.autoRotate = true;
      }
    }
  }

  requestAnimationFrame(animateCamera);
}