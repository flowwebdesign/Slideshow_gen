(() => {
  "use strict";
  const MAX_PHOTOS = 100;
  const MAX_PHOTO_BYTES = 20 * 1024 * 1024;
  const MAX_TOTAL_BYTES = 500 * 1024 * 1024;
  const ACTIVE_JOB_KEY = "slideshowActiveJob";
  const form = document.querySelector("#slideshow-form");
  const input = document.querySelector("#photo-input");
  const dropZone = document.querySelector("#upload-zone");
  const choosePhotos = document.querySelector("#choose-photos");
  const list = document.querySelector("#photo-list");
  const count = document.querySelector("#photo-count");
  const summary = document.querySelector("#photo-summary");
  const submit = document.querySelector("#create-button");
  const error = document.querySelector("#form-error");
  const progressPanel = document.querySelector("#progress-panel");
  const progressBar = document.querySelector("#progress-bar");
  const progressNumber = document.querySelector("#progress-number");
  const progressMessage = document.querySelector("#progress-message");
  const progressTrack = document.querySelector(".progress-track");
  const resultPanel = document.querySelector("#result-panel");
  const skippedWarning = document.querySelector("#skipped-warning");
  const durationChoice = document.querySelector("#duration-choice");
  const customDurationLabel = document.querySelector("#custom-duration-label");
  const customDuration = document.querySelector("#custom-duration");
  const style = document.querySelector("#style");
  const customSettings = document.querySelector("#custom-settings");
  const simpleView = document.querySelector("#simple-view");
  const advancedView = document.querySelector("#advanced-view");
  const viewDescription = document.querySelector("#view-description");
  const videoEstimate = document.querySelector("#video-estimate");
  const videoQuality = document.querySelector("#video-quality");
  const titleMode = document.querySelector("#title-mode");
  const titlePhoto = document.querySelector("#title-photo");
  const titleStart = document.querySelector("#title-start");
  const titleDuration = document.querySelector("#title-duration");
  const titlePhotoLabel = document.querySelector("#title-photo-label");
  const titleStartLabel = document.querySelector("#title-start-label");
  const titleDurationLabel = document.querySelector("#title-duration-label");
  const titleSize = document.querySelector("#title-size");
  const subtitleSize = document.querySelector("#subtitle-size");
  const captionSize = document.querySelector("#caption-size");
  const textColor = document.querySelector("#text-color");
  const panelOpacity = document.querySelector("#panel-opacity");
  const textAlign = document.querySelector("#text-align");
  const textAnimation = document.querySelector("#text-animation");
  const titleX = document.querySelector("#title-x");
  const titleY = document.querySelector("#title-y");
  const previewStage = document.querySelector("#design-preview");
  const previewBackdrop = document.querySelector("#preview-backdrop");
  const previewPhoto = document.querySelector("#preview-photo");
  const previewTitleLayer = document.querySelector("#preview-title-layer");
  const previewTitle = document.querySelector("#preview-title");
  const previewSubtitle = document.querySelector("#preview-subtitle");
  const previewCaption = document.querySelector("#preview-caption");
  const menuToggle = document.querySelector("#menu-toggle");
  const siteMenu = document.querySelector("#site-menu");
  const settingsLink = document.querySelector("#settings-link");
  const diagnosticPanel = document.querySelector("#upload-diagnostics");
  const diagnosticOutput = document.querySelector("#diagnostic-output");
  const runUploadCheck = document.querySelector("#run-upload-check");
  const copyDiagnostics = document.querySelector("#copy-diagnostics");
  const fontFamilies = {
    modern: "Preview Modern", friendly: "Preview Friendly", elegant: "Preview Elegant",
    cinematic: "Preview Cinematic", classic: "Preview Classic",
    typewriter: "Preview Typewriter", "large-tv": "Preview Large TV"
  };
  let photos = [];
  let draggedIndex = null;
  let draggingTitle = false;
  let uploadDiagnostics = [];
  let skippedPhotos = 0;

  function diagnostic(message) {
    const clock = new Date().toISOString().slice(11, 19);
    uploadDiagnostics.push(`[${clock}] ${message}`);
    diagnosticOutput.textContent = uploadDiagnostics.join("\n");
  }

  function beginDiagnostics() {
    uploadDiagnostics = [];
    const total = photos.reduce((sum, photo) => sum + photo.file.size, 0);
    diagnostic(`Browser online: ${navigator.onLine ? "yes" : "no"}`);
    diagnostic(`Photos: ${photos.length}; total: ${(total / 1024 / 1024).toFixed(1)} MB`);
    diagnostic("Upload mode: resilient one-photo requests; retry limit: 3");
  }

  async function responseJson(response, action) {
    let data = {};
    try { data = await response.json(); } catch (_problem) { /* The status still identifies upstream failures. */ }
    const requestId = response.headers.get("X-Request-ID") || data.request_id || "none (upstream interruption)";
    diagnostic(`${action}: HTTP ${response.status}; request ID: ${requestId}`);
    if (!response.ok) {
      const problem = new Error(data.detail || data.error || `${action} failed with HTTP ${response.status}`);
      problem.status = response.status;
      problem.requestId = requestId;
      throw problem;
    }
    return data;
  }

  function rememberActiveJob(jobId) {
    try { window.localStorage.setItem(ACTIVE_JOB_KEY, jobId); } catch (_problem) { /* Recovery is best-effort. */ }
  }

  function clearActiveJob() {
    try { window.localStorage.removeItem(ACTIVE_JOB_KEY); } catch (_problem) { /* Recovery is best-effort. */ }
  }

  function storedActiveJob() {
    try {
      const jobId = window.localStorage.getItem(ACTIVE_JOB_KEY) || "";
      return /^[A-Za-z0-9_-]{22}$/.test(jobId) ? jobId : "";
    } catch (_problem) {
      return "";
    }
  }

  function showError(message) {
    error.textContent = message;
    error.hidden = !message;
    if (message) error.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function validateNewFiles(files) {
    if (photos.length + files.length > MAX_PHOTOS) return "You can choose no more than 100 photos.";
    let total = photos.reduce((sum, photo) => sum + photo.file.size, 0);
    for (const file of files) {
      if (file.size > MAX_PHOTO_BYTES) return `${file.name} is larger than 20 MB.`;
      total += file.size;
    }
    if (total > MAX_TOTAL_BYTES) return "The selected photos are larger than 500 MB in total.";
    return "";
  }

  function addFiles(fileList) {
    const files = Array.from(fileList);
    const validation = validateNewFiles(files);
    if (validation) return showError(validation);
    showError("");
    files.forEach(file => photos.push({ file, rotation: 0, caption: "", url: URL.createObjectURL(file) }));
    input.value = "";
    renderPhotos();
  }

  function movePhoto(from, to) {
    if (to < 0 || to >= photos.length || from === to) return;
    const [photo] = photos.splice(from, 1);
    photos.splice(to, 0, photo);
    renderPhotos();
  }

  function button(label, title, action, className = "") {
    const element = document.createElement("button");
    element.type = "button";
    element.textContent = label;
    element.title = title;
    element.setAttribute("aria-label", title);
    element.className = className;
    element.addEventListener("click", action);
    return element;
  }

  function renderPhotos() {
    list.replaceChildren();
    photos.forEach((photo, index) => {
      const card = document.createElement("article");
      card.className = "photo-card";
      card.draggable = true;
      card.dataset.index = index;
      const image = document.createElement("img");
      image.src = photo.url;
      image.alt = `Selected photo ${index + 1}`;
      image.style.transform = `rotate(${photo.rotation}deg)`;
      const number = document.createElement("span");
      number.className = "photo-number";
      number.textContent = index + 1;
      const body = document.createElement("div");
      body.className = "photo-card-body";
      const controls = document.createElement("div");
      controls.className = "card-controls";
      controls.append(
        button("←", `Move photo ${index + 1} earlier`, () => movePhoto(index, index - 1)),
        button("→", `Move photo ${index + 1} later`, () => movePhoto(index, index + 1)),
        button("↻", `Rotate photo ${index + 1} right`, () => { photo.rotation = (photo.rotation + 90) % 360; renderPhotos(); }),
        button("×", `Remove photo ${index + 1}`, () => { URL.revokeObjectURL(photo.url); photos.splice(index, 1); renderPhotos(); }, "remove")
      );
      const captionLabel = document.createElement("label");
      captionLabel.className = "caption-label";
      captionLabel.textContent = "Optional caption";
      const caption = document.createElement("input");
      caption.type = "text";
      caption.maxLength = 300;
      caption.value = photo.caption;
      caption.placeholder = "Who or where is this?";
      caption.addEventListener("input", event => { photo.caption = event.target.value; updateAdvancedPreview(); });
      captionLabel.append(caption);
      body.append(controls, captionLabel);
      card.append(image, number, body);
      card.addEventListener("dragstart", () => { draggedIndex = index; card.classList.add("dragging"); });
      card.addEventListener("dragend", () => { draggedIndex = null; card.classList.remove("dragging"); });
      card.addEventListener("dragover", event => event.preventDefault());
      card.addEventListener("drop", event => { event.preventDefault(); if (draggedIndex !== null) movePhoto(draggedIndex, index); });
      list.append(card);
    });
    count.textContent = `${photos.length} photo${photos.length === 1 ? "" : "s"} selected`;
    summary.hidden = photos.length === 0;
    submit.disabled = photos.length === 0;
    syncTitlePhotoOptions();
    updateEstimate();
    updateAdvancedPreview();
  }

  function syncTitlePhotoOptions() {
    const previous = Number(titlePhoto.value || 0);
    titlePhoto.replaceChildren();
    if (!photos.length) {
      titlePhoto.append(new Option("Choose photos first", "0"));
      titlePhoto.disabled = true;
      return;
    }
    photos.forEach((_photo, index) => titlePhoto.append(new Option(`Photo ${index + 1}`, String(index))));
    titlePhoto.value = String(Math.min(previous, photos.length - 1));
    titlePhoto.disabled = false;
  }

  function updateTitleControls() {
    const overlay = titleMode.value === "overlay";
    const hidden = titleMode.value === "hidden";
    titlePhotoLabel.hidden = !overlay;
    titleStartLabel.hidden = !overlay;
    titleDurationLabel.hidden = hidden;
  }

  function updateAdvancedPreview() {
    updateTitleControls();
    const ratio = document.querySelector("#aspect-ratio").value.replace(":", " / ");
    previewStage.style.aspectRatio = ratio;
    previewStage.classList.toggle("card-mode", titleMode.value === "card");
    const photo = photos[Number(titlePhoto.value || 0)] || photos[0];
    if (photo && titleMode.value !== "card") {
      previewPhoto.src = photo.url;
      previewBackdrop.style.backgroundImage = `url("${photo.url}")`;
      previewPhoto.hidden = false;
      previewBackdrop.hidden = document.querySelector("#background").value === "black";
    } else {
      previewPhoto.removeAttribute("src");
      previewPhoto.hidden = true;
      previewBackdrop.hidden = true;
    }
    previewStage.style.background = document.querySelector("#background").value === "black" && titleMode.value !== "card"
      ? "#000" : "";
    previewTitle.textContent = document.querySelector("#title").value.trim() || "Our family memories";
    previewSubtitle.textContent = document.querySelector("#subtitle").value.trim() || "Summer 2026";
    const hasTitle = Boolean(document.querySelector("#title").value.trim());
    const hasSubtitle = Boolean(document.querySelector("#subtitle").value.trim());
    previewTitle.hidden = false;
    previewSubtitle.hidden = false;
    previewTitleLayer.hidden = titleMode.value === "hidden";
    previewTitleLayer.classList.toggle("placeholder-copy", !hasTitle && !hasSubtitle);
    const family = fontFamilies[styleFont().value] || fontFamilies.modern;
    previewStage.style.setProperty("--preview-font", `"${family}"`);
    const stageHeight = previewStage.clientHeight || 360;
    previewTitle.style.fontSize = `${Math.max(15, stageHeight * 0.048 * Number(titleSize.value) / 100)}px`;
    previewSubtitle.style.fontSize = `${Math.max(12, stageHeight * 0.048 * Number(subtitleSize.value) / 100)}px`;
    previewCaption.style.fontSize = `${Math.max(11, stageHeight * 0.048 * Number(captionSize.value) / 100)}px`;
    previewTitleLayer.style.left = `${titleX.value}%`;
    previewTitleLayer.style.top = `${titleY.value}%`;
    previewTitleLayer.style.color = textColor.value;
    previewCaption.style.color = textColor.value;
    const alignment = textAlign.value === "centre" ? "center" : textAlign.value;
    previewTitleLayer.style.textAlign = alignment;
    previewCaption.style.textAlign = alignment;
    const alpha = Number(panelOpacity.value) / 100;
    const panel = `rgba(0, 0, 0, ${alpha})`;
    previewTitleLayer.style.background = panel;
    previewCaption.style.background = panel;
    const caption = photo?.caption?.trim();
    previewCaption.textContent = caption || "Caption preview";
    previewCaption.style.opacity = caption ? "1" : ".62";
    const captionPosition = document.querySelector("#text-position").value;
    previewCaption.style.top = captionPosition === "top" ? "8%" : captionPosition === "centre" ? "50%" : "auto";
    previewCaption.style.bottom = captionPosition === "bottom" ? "8%" : "auto";
    previewCaption.style.transform = captionPosition === "centre" ? "translate(-50%, -50%)" : "translateX(-50%)";
    previewTitleLayer.classList.toggle("preview-fade", textAnimation.value === "fade");
    document.querySelector("#title-size-output").textContent = `${titleSize.value}%`;
    document.querySelector("#subtitle-size-output").textContent = `${subtitleSize.value}%`;
    document.querySelector("#caption-size-output").textContent = `${captionSize.value}%`;
    document.querySelector("#panel-opacity-output").textContent = `${panelOpacity.value}%`;
    document.querySelector("#title-x-output").textContent = `${titleX.value}%`;
    document.querySelector("#title-y-output").textContent = `${titleY.value}%`;
    document.querySelectorAll("#position-grid button").forEach(button => {
      button.classList.toggle("active", button.dataset.x === titleX.value && button.dataset.y === titleY.value);
    });
  }

  function styleFont() {
    return document.querySelector("#font");
  }

  function selectedDuration() {
    return durationChoice.value === "custom" ? Number(customDuration.value) : Number(durationChoice.value);
  }

  function formatDuration(seconds) {
    const rounded = Math.max(0, Math.ceil(seconds));
    const minutes = Math.floor(rounded / 60);
    const remainder = rounded % 60;
    if (!minutes) return `${remainder} second${remainder === 1 ? "" : "s"}`;
    if (!remainder) return `${minutes} minute${minutes === 1 ? "" : "s"}`;
    return `${minutes} minute${minutes === 1 ? "" : "s"} ${remainder} seconds`;
  }

  function updateEstimate() {
    if (!photos.length) {
      videoEstimate.textContent = "Add photos to see the estimated video length.";
      return;
    }
    const duration = selectedDuration();
    if (!Number.isFinite(duration) || duration < 1 || duration > 20) {
      videoEstimate.textContent = "Enter a photo time between 1 and 20 seconds.";
      return;
    }
    const hasTitle = (document.querySelector("#title").value.trim() || document.querySelector("#subtitle").value.trim())
      && titleMode.value !== "hidden";
    const seconds = photos.length * duration + (hasTitle && titleMode.value === "card" ? Number(titleDuration.value) : 0);
    const qualityNote = videoQuality.value === "4k"
      ? " 4K preserves more detail and will take longer to render."
      : " Full HD renders faster and creates a smaller file.";
    videoEstimate.textContent = `Estimated video length: ${formatDuration(seconds)}.${qualityNote}`;
  }

  input.addEventListener("change", () => addFiles(input.files));
  choosePhotos.addEventListener("click", () => input.click());
  ["dragenter", "dragover"].forEach(name => dropZone.addEventListener(name, event => { event.preventDefault(); dropZone.classList.add("dragging"); }));
  ["dragleave", "drop"].forEach(name => dropZone.addEventListener(name, event => { event.preventDefault(); dropZone.classList.remove("dragging"); }));
  dropZone.addEventListener("drop", event => addFiles(event.dataTransfer.files));
  durationChoice.addEventListener("change", () => {
    customDurationLabel.hidden = durationChoice.value !== "custom";
    updateEstimate();
  });
  customDuration.addEventListener("input", updateEstimate);
  videoQuality.addEventListener("change", updateEstimate);
  document.querySelector("#title").addEventListener("input", () => { updateEstimate(); updateAdvancedPreview(); });
  document.querySelector("#subtitle").addEventListener("input", () => { updateEstimate(); updateAdvancedPreview(); });
  [titleMode, titlePhoto, titleStart, titleDuration].forEach(element => element.addEventListener("input", () => {
    updateEstimate();
    updateAdvancedPreview();
  }));
  const customDesignControls = [titleSize, subtitleSize, captionSize, textColor, panelOpacity, textAlign,
    textAnimation, titleX, titleY, styleFont(), document.querySelector("#transition"),
    document.querySelector("#movement"), document.querySelector("#text-position")];
  customDesignControls.forEach(element => element.addEventListener("input", () => {
    style.value = "custom";
    updateAdvancedPreview();
  }));
  [styleFont(), document.querySelector("#aspect-ratio"), document.querySelector("#background"), document.querySelector("#text-position")]
    .forEach(element => element.addEventListener("change", updateAdvancedPreview));
  document.querySelectorAll("#position-grid button").forEach(button => button.addEventListener("click", () => {
    titleX.value = button.dataset.x;
    titleY.value = button.dataset.y;
    style.value = "custom";
    updateAdvancedPreview();
  }));

  previewTitleLayer.addEventListener("pointerdown", event => {
    draggingTitle = true;
    previewTitleLayer.setPointerCapture(event.pointerId);
  });
  previewTitleLayer.addEventListener("pointermove", event => {
    if (!draggingTitle) return;
    const bounds = previewStage.getBoundingClientRect();
    titleX.value = String(Math.round(Math.max(5, Math.min(95, (event.clientX - bounds.left) / bounds.width * 100))));
    titleY.value = String(Math.round(Math.max(5, Math.min(95, (event.clientY - bounds.top) / bounds.height * 100))));
    style.value = "custom";
    updateAdvancedPreview();
  });
  ["pointerup", "pointercancel"].forEach(name => previewTitleLayer.addEventListener(name, () => { draggingTitle = false; }));
  previewTitleLayer.addEventListener("keydown", event => {
    const moves = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    if (!moves[event.key]) return;
    event.preventDefault();
    titleX.value = String(Math.max(5, Math.min(95, Number(titleX.value) + moves[event.key][0])));
    titleY.value = String(Math.max(5, Math.min(95, Number(titleY.value) + moves[event.key][1])));
    style.value = "custom";
    updateAdvancedPreview();
  });
  function setAdvancedView(enabled) {
    simpleView.setAttribute("aria-pressed", String(!enabled));
    advancedView.setAttribute("aria-pressed", String(enabled));
    customSettings.hidden = !enabled;
    customSettings.open = enabled;
    viewDescription.textContent = enabled
      ? "Advanced view lets you preview and position text, choose fonts and sizes, and control movement and transitions."
      : "Simple view keeps the recommended settings and the essential choices.";
    if (enabled) window.requestAnimationFrame(updateAdvancedPreview);
  }
  simpleView.addEventListener("click", () => setAdvancedView(false));
  advancedView.addEventListener("click", () => setAdvancedView(true));
  style.addEventListener("change", () => { if (style.value === "custom") setAdvancedView(true); });
  menuToggle.addEventListener("click", () => {
    const open = !siteMenu.classList.contains("open");
    siteMenu.classList.toggle("open", open);
    menuToggle.setAttribute("aria-expanded", String(open));
  });
  siteMenu.querySelectorAll("a").forEach(link => link.addEventListener("click", () => {
    siteMenu.classList.remove("open");
    menuToggle.setAttribute("aria-expanded", "false");
  }));
  settingsLink.addEventListener("click", () => setAdvancedView(true));

  function setProgress(value, message) {
    const rounded = Math.max(0, Math.min(100, Math.round(value)));
    progressPanel.style.setProperty("--rob-scale", String(0.88 + rounded * 0.004));
    progressBar.style.width = `${rounded}%`;
    progressNumber.textContent = `${rounded}%`;
    progressTrack.setAttribute("aria-valuenow", rounded);
    if (message) progressMessage.textContent = message;
  }

  function settingsPayload() {
    const duration = durationChoice.value === "custom" ? Number(customDuration.value) : Number(durationChoice.value);
    return {
      title: document.querySelector("#title").value,
      subtitle: document.querySelector("#subtitle").value,
      duration,
      aspect_ratio: document.querySelector("#aspect-ratio").value,
      video_quality: videoQuality.value,
      background: document.querySelector("#background").value,
      style: style.value,
      transition: document.querySelector("#transition").value,
      movement: document.querySelector("#movement").value,
      font: document.querySelector("#font").value,
      text_position: document.querySelector("#text-position").value,
      title_mode: titleMode.value,
      title_photo_index: Number(titlePhoto.value || 0),
      title_start: Number(titleStart.value),
      title_duration: Number(titleDuration.value),
      title_size: Number(titleSize.value) / 100,
      subtitle_size: Number(subtitleSize.value) / 100,
      caption_size: Number(captionSize.value) / 100,
      text_x: Number(titleX.value) / 100,
      text_y: Number(titleY.value) / 100,
      text_color: textColor.value,
      text_panel_opacity: Math.round(Number(panelOpacity.value) * 2.55),
      text_align: textAlign.value,
      text_animation: textAnimation.value,
      rotations: photos.map(photo => photo.rotation),
      captions: photos.map(photo => photo.caption)
    };
  }

  function uploadAttempt(jobId, token, photo, index, totalBytes, uploadedBefore, attempt) {
    return new Promise((resolve, reject) => {
      const body = new FormData();
      body.append("file", photo.file, photo.file.name);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/uploads/${jobId}/files/${index}`);
      xhr.responseType = "json";
      xhr.timeout = 5 * 60 * 1000;
      xhr.setRequestHeader("X-Job-Token", token);
      xhr.upload.addEventListener("progress", progress => {
        const current = progress.lengthComputable ? progress.loaded : 0;
        setProgress(
          Math.max(1, (uploadedBefore + current) / totalBytes * 10),
          `Uploading photo ${index + 1} of ${photos.length} (attempt ${attempt})`,
        );
      });
      xhr.addEventListener("load", () => {
        const requestId = xhr.getResponseHeader("X-Request-ID")
          || xhr.response?.request_id || "none (upstream interruption)";
        diagnostic(`Photo ${index + 1}, attempt ${attempt}: HTTP ${xhr.status}; request ID: ${requestId}`);
        if (xhr.status >= 200 && xhr.status < 300) return resolve(xhr.response || {});
        const problem = new Error(xhr.response?.detail || xhr.response?.error || `HTTP ${xhr.status}`);
        problem.status = xhr.status;
        problem.retryable = xhr.status === 408 || xhr.status === 429 || xhr.status >= 500;
        reject(problem);
      });
      xhr.addEventListener("error", () => {
        diagnostic(`Photo ${index + 1}, attempt ${attempt}: network interruption; no application response`);
        const problem = new Error("the connection was interrupted before the server responded");
        problem.retryable = true;
        reject(problem);
      });
      xhr.addEventListener("timeout", () => {
        diagnostic(`Photo ${index + 1}, attempt ${attempt}: timed out after 5 minutes`);
        const problem = new Error("one photo took longer than 5 minutes to upload");
        problem.retryable = true;
        reject(problem);
      });
      xhr.send(body);
    });
  }

  async function uploadPhotoWithRetry(jobId, token, photo, index, totalBytes, uploadedBefore) {
    let lastProblem;
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        return await uploadAttempt(jobId, token, photo, index, totalBytes, uploadedBefore, attempt);
      } catch (problem) {
        lastProblem = problem;
        if (!problem.retryable || attempt === 3) break;
        diagnostic(`Photo ${index + 1}: retrying after attempt ${attempt}`);
        await new Promise(resolve => window.setTimeout(resolve, attempt * 1000));
      }
    }
    lastProblem.photoNumber = index + 1;
    throw lastProblem;
  }

  runUploadCheck.addEventListener("click", async () => {
    diagnosticPanel.open = true;
    runUploadCheck.disabled = true;
    diagnostic("Connection check started (512 KB)");
    const started = performance.now();
    try {
      const response = await fetch("/api/upload-check", {
        method: "POST", headers: { "Content-Type": "application/octet-stream" },
        body: new Uint8Array(512 * 1024),
      });
      const data = await responseJson(response, "Connection check");
      diagnostic(`Connection check passed: ${data.bytes_received} bytes in ${Math.round(performance.now() - started)} ms`);
    } catch (problem) {
      diagnostic(`Connection check failed: ${problem.message}`);
    } finally {
      runUploadCheck.disabled = false;
    }
  });

  copyDiagnostics.addEventListener("click", async () => {
    const report = diagnosticOutput.textContent;
    try {
      await navigator.clipboard.writeText(report);
      copyDiagnostics.textContent = "Copied";
      window.setTimeout(() => { copyDiagnostics.textContent = "Copy diagnostics"; }, 1800);
    } catch (_problem) {
      diagnostic("Copy was blocked by the browser; select the report text manually");
    }
  });

  function pollJob(jobId, token = "") {
    let networkFailures = 0;
    let lastReportedState = "";
    let lastReportedProgress = -1;
    const poll = async () => {
      try {
        const headers = token ? { "X-Job-Token": token } : {};
        const response = await fetch(`/api/jobs/${jobId}/status`, { headers, cache: "no-store" });
        const data = await response.json();
        if (!response.ok) {
          if ([401, 403, 404].includes(response.status)) clearActiveJob();
          throw new Error(data.detail || data.error || "Could not check the slideshow");
        }
        networkFailures = 0;
        if (data.state !== lastReportedState || data.progress !== lastReportedProgress) {
          diagnostic(
            `Job state=${data.state}; progress=${data.progress}%; server updated=${data.updated_at || "unknown"}`,
          );
          lastReportedState = data.state;
          lastReportedProgress = data.progress;
        }
        const messages = {
          queued: "Waiting for the video maker",
          preparing: "Preparing your photos",
          ready: "Finished",
          downloaded: "Finished",
        };
        let progressText = messages[data.state] || "Working";
        if (data.state === "rendering") {
          progressText = data.progress >= 99
            ? "Final checks — almost ready"
            : data.progress >= 95
              ? "Finishing the video"
              : "Creating the video";
        }
        setProgress(data.progress, progressText);
        if (data.state === "ready" || data.state === "downloaded") {
          progressPanel.hidden = true;
          resultPanel.hidden = false;
          skippedWarning.hidden = skippedPhotos === 0;
          const acceptedPhotos = photos.length - skippedPhotos;
          skippedWarning.textContent = `${acceptedPhotos} photo${acceptedPhotos === 1 ? "" : "s"} uploaded; `
            + `${skippedPhotos} photo${skippedPhotos === 1 ? "" : "s"} could not be processed.`;
          const preview = document.querySelector("#video-preview");
          preview.src = `/api/jobs/${jobId}/preview`;
          preview.onerror = () => {
            const previewError = document.querySelector("#preview-error");
            previewError.textContent = "The preview could not be loaded. Your video may have expired; try downloading it or create it again.";
            previewError.hidden = false;
          };
          const download = document.querySelector("#download-button");
          download.href = `/api/jobs/${jobId}/download`;
          download.onclick = () => { document.querySelector("#youtube-help").hidden = false; };
          resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
          return;
        }
        if (data.state === "failed" || data.state === "expired") {
          clearActiveJob();
          throw new Error(data.error || "The slideshow could not be created. Please try again.");
        }
        window.setTimeout(poll, 1000);
      } catch (problem) {
        networkFailures += 1;
        if (problem instanceof TypeError && networkFailures <= 6) {
          const waitSeconds = Math.min(10, networkFailures * 2);
          setProgress(Number(progressTrack.getAttribute("aria-valuenow")), `Connection interrupted. Retrying in ${waitSeconds} seconds…`);
          window.setTimeout(poll, waitSeconds * 1000);
          return;
        }
        progressPanel.hidden = true;
        form.hidden = false;
        submit.disabled = false;
        showError(problem instanceof TypeError
          ? "The server could not be reached after several attempts. Check that it is running, then try again."
          : problem.message);
      }
    };
    poll();
  }

  form.addEventListener("submit", async event => {
    event.preventDefault();
    showError("");
    if (!photos.length) return showError("Choose at least one photo.");
    const settings = settingsPayload();
    if (!Number.isFinite(settings.duration) || settings.duration < 1 || settings.duration > 20) return showError("Photo time must be between 1 and 20 seconds.");
    if ((settings.title || settings.subtitle) && settings.title_mode === "overlay") {
      if (settings.title_photo_index >= photos.length) return showError("Choose a photo for the title overlay.");
      if (!Number.isFinite(settings.title_start) || !Number.isFinite(settings.title_duration)
          || settings.title_start < 0 || settings.title_duration < 0.5
          || settings.title_start + settings.title_duration > settings.duration) {
        return showError("The title timing must fit within the selected photo time.");
      }
    }
    const estimated = photos.length * settings.duration
      + ((settings.title || settings.subtitle) && settings.title_mode === "card" ? settings.title_duration : 0);
    if (estimated > 1200) return showError("This slideshow would be longer than 20 minutes. Use fewer photos or a shorter time.");
    submit.disabled = true;
    form.hidden = true;
    progressPanel.hidden = false;
    progressPanel.scrollIntoView({ behavior: "smooth", block: "center" });
    beginDiagnostics();
    skippedPhotos = 0;
    const totalBytes = photos.reduce((sum, photo) => sum + photo.file.size, 0);
    try {
      const startResponse = await fetch("/api/uploads", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ photo_count: photos.length, settings }),
      });
      const upload = await responseJson(startResponse, "Start upload");
      diagnostic(`Job reference: ${upload.job_id}`);
      const reference = document.querySelector("#job-reference");
      reference.textContent = `Job reference: ${upload.job_id}`;
      reference.hidden = false;
      let uploadedBytes = 0;
      for (let index = 0; index < photos.length; index += 1) {
        try {
          const received = await uploadPhotoWithRetry(
            upload.job_id, upload.access_token, photos[index], index, totalBytes, uploadedBytes,
          );
          if (received.status === "skipped") {
            skippedPhotos += 1;
            const decoder = received.error?.detected_format || "unknown";
            const code = received.error?.code || "invalid_image";
            const detail = received.error?.detail || received.reason;
            diagnostic(
              `Photo ${index + 1} (${photos[index].file.name}) skipped; `
              + `code=${code}; detected format=${decoder}; detail=${detail}`,
            );
            setProgress(
              Math.max(1, (uploadedBytes + photos[index].file.size) / totalBytes * 10),
              received.reason,
            );
          }
        } catch (problem) {
          if (problem.status !== 415) throw problem;
          skippedPhotos += 1;
          diagnostic(`Photo ${index + 1} skipped: ${problem.message}`);
          setProgress(
            Math.max(1, (uploadedBytes + photos[index].file.size) / totalBytes * 10),
            `Photo ${index + 1} could not be decoded and was skipped`,
          );
        }
        uploadedBytes += photos[index].file.size;
      }
      const completeResponse = await fetch(`/api/uploads/${upload.job_id}/complete`, {
        method: "POST", headers: { "X-Job-Token": upload.access_token },
      });
      const completed = await responseJson(completeResponse, "Complete upload");
      skippedPhotos = completed.skipped_photos ?? skippedPhotos;
      const acceptedPhotos = completed.accepted_photos ?? photos.length - skippedPhotos;
      diagnostic(
        `${acceptedPhotos} photos accepted; ${completed.failed_photos ?? skippedPhotos} failed; rendering started`,
      );
      setProgress(10, "Upload complete. Preparing your photos");
      rememberActiveJob(upload.job_id);
      pollJob(upload.job_id, upload.access_token);
    } catch (problem) {
      progressPanel.hidden = true;
      form.hidden = false;
      submit.disabled = false;
      diagnosticPanel.open = true;
      const location = problem.photoNumber ? ` at photo ${problem.photoNumber} of ${photos.length}` : "";
      diagnostic(`Upload stopped${location}: ${problem.message}`);
      showError(`Upload stopped${location}: ${problem.message}. Open Upload help & diagnostics below the photo chooser for the error report.`);
    }
  });

  window.addEventListener("beforeunload", () => photos.forEach(photo => URL.revokeObjectURL(photo.url)));
  document.querySelector("#fullscreen-button").addEventListener("click", async () => {
    const preview = document.querySelector("#video-preview");
    const previewError = document.querySelector("#preview-error");
    try {
      if (preview.requestFullscreen) await preview.requestFullscreen();
      else if (preview.webkitEnterFullscreen) preview.webkitEnterFullscreen();
      await preview.play();
    } catch (_problem) {
      previewError.textContent = "Full-screen playback could not start automatically. Press play, then use the full-screen icon in the video controls.";
      previewError.hidden = false;
    }
  });

  document.querySelector("#create-another-button").addEventListener("click", () => {
    clearActiveJob();
    photos.forEach(photo => URL.revokeObjectURL(photo.url));
    photos = [];
    form.reset();
    setAdvancedView(false);
    customDurationLabel.hidden = true;
    document.querySelector("#youtube-help").hidden = true;
    document.querySelector("#preview-error").hidden = true;
    skippedWarning.hidden = true;
    skippedPhotos = 0;
    document.querySelector("#job-reference").hidden = true;
    const preview = document.querySelector("#video-preview");
    preview.pause();
    preview.removeAttribute("src");
    preview.load();
    resultPanel.hidden = true;
    progressPanel.hidden = true;
    form.hidden = false;
    showError("");
    renderPhotos();
    form.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  const activeJobId = storedActiveJob();
  if (activeJobId) {
    form.hidden = true;
    progressPanel.hidden = false;
    const reference = document.querySelector("#job-reference");
    reference.textContent = `Job reference: ${activeJobId}`;
    reference.hidden = false;
    setProgress(10, "Restoring your slideshow");
    pollJob(activeJobId);
  } else {
    updateEstimate();
    syncTitlePhotoOptions();
    updateAdvancedPreview();
  }
})();
