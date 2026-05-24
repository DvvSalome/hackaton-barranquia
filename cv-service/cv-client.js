/**
 * KairosCV — browser client for cv-service
 *
 * Usage:
 *   import { KairosCV } from './cv-client.js';
 *   const cv = new KairosCV('http://localhost:8002');
 *   const result = await cv.analyzeFrame(videoElement);
 *
 * Or use the continuous mode:
 *   cv.startLoop(videoElement, (result) => console.log(result), 2000);
 *   cv.stopLoop();
 */

export class KairosCV {
  /**
   * @param {string} baseUrl — cv-service base URL (default: http://localhost:8002)
   * @param {Object} options
   * @param {number} options.jpegQuality — 0.0-1.0, default 0.7
   * @param {number} options.maxWidth    — resize before sending, default 640
   */
  constructor(baseUrl = 'http://localhost:8002', options = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.jpegQuality = options.jpegQuality ?? 0.7;
    this.maxWidth = options.maxWidth ?? 640;
    this._loopTimer = null;
    this._canvas = document.createElement('canvas');
    this._ctx = this._canvas.getContext('2d');
  }

  /** Capture a frame from a <video> element and return base64 JPEG. */
  _captureFrame(videoEl) {
    const w = Math.min(videoEl.videoWidth, this.maxWidth);
    const scale = w / videoEl.videoWidth;
    const h = Math.round(videoEl.videoHeight * scale);
    this._canvas.width = w;
    this._canvas.height = h;
    this._ctx.drawImage(videoEl, 0, 0, w, h);
    return this._canvas.toDataURL('image/jpeg', this.jpegQuality);
  }

  /** Run full CV analysis (objects + gestures + face) on a video frame. */
  async analyzeFrame(videoEl) {
    const b64 = this._captureFrame(videoEl);
    const res = await fetch(`${this.baseUrl}/cv/analyze/b64`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_b64: b64 }),
    });
    if (!res.ok) throw new Error(`CV service error: ${res.status}`);
    return res.json();
  }

  /** Run only object + person detection. */
  async detectObjects(videoEl) {
    const b64 = this._captureFrame(videoEl);
    const res = await fetch(`${this.baseUrl}/cv/objects/b64`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_b64: b64 }),
    });
    if (!res.ok) throw new Error(`CV service error: ${res.status}`);
    return res.json();
  }

  /** Run only gesture + pose detection. */
  async detectGestures(videoEl) {
    const b64 = this._captureFrame(videoEl);
    const res = await fetch(`${this.baseUrl}/cv/gestures/b64`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_b64: b64 }),
    });
    if (!res.ok) throw new Error(`CV service error: ${res.status}`);
    return res.json();
  }

  /** Run only face / drowsiness / gaze detection. */
  async detectFace(videoEl) {
    const b64 = this._captureFrame(videoEl);
    const res = await fetch(`${this.baseUrl}/cv/face/b64`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_b64: b64 }),
    });
    if (!res.ok) throw new Error(`CV service error: ${res.status}`);
    return res.json();
  }

  /**
   * Start continuous analysis loop.
   * @param {HTMLVideoElement} videoEl
   * @param {function} onResult — callback(result)
   * @param {number} intervalMs — milliseconds between frames, default 2000
   */
  startLoop(videoEl, onResult, intervalMs = 2000) {
    this.stopLoop();
    const tick = async () => {
      try {
        const result = await this.analyzeFrame(videoEl);
        onResult(result);
      } catch (err) {
        console.warn('[KairosCV] frame error:', err.message);
      }
      this._loopTimer = setTimeout(tick, intervalMs);
    };
    tick();
  }

  stopLoop() {
    if (this._loopTimer) {
      clearTimeout(this._loopTimer);
      this._loopTimer = null;
    }
  }

  /** Health check — returns true if cv-service is up. */
  async ping() {
    try {
      const res = await fetch(`${this.baseUrl}/cv/health`);
      return res.ok;
    } catch {
      return false;
    }
  }
}

/**
 * Quick helper: open user camera, run CV analysis every N ms, call onResult.
 *
 * @param {function} onResult  — called with each analysis result
 * @param {number}   intervalMs
 * @param {string}   cvBaseUrl
 * @returns {{ video: HTMLVideoElement, cv: KairosCV, stop: function }}
 */
export async function startCameraAnalysis(onResult, intervalMs = 2000, cvBaseUrl = 'http://localhost:8002') {
  const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 } });
  const video = document.createElement('video');
  video.srcObject = stream;
  video.muted = true;
  video.playsInline = true;
  await video.play();

  const cv = new KairosCV(cvBaseUrl);
  cv.startLoop(video, onResult, intervalMs);

  return {
    video,
    cv,
    stop: () => {
      cv.stopLoop();
      stream.getTracks().forEach(t => t.stop());
    },
  };
}
