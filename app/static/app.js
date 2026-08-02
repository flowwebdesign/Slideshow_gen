(() => {
  "use strict";
  const MAX_PHOTOS = 100;
  const MAX_PHOTO_BYTES = 20 * 1024 * 1024;
  const MAX_TOTAL_BYTES = 500 * 1024 * 1024;
  const ACTIVE_JOB_KEY = "slideshowActiveJob";
  const allowedExtensions = new Set(["jpg", "jpeg", "png", "webp", "heic", "heif"]);
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
  const durationChoice = document.querySelector("#duration-choice");
  const customDurationLabel = document.querySelector("#custom-duration-label");
  const customDuration = document.querySelector("#custom-duration");
  const style = document.querySelector("#style");
  const customSettings = document.querySelector("#custom-settings");
  const simpleView = document.querySelector("#simple-view");
  const advancedView = document.querySelector("#advanced-view");
  const viewDescription = document.querySelector("#view-description");
  const videoEstimate = document.querySelector("#video-estimate");
  let photos = [];
  let draggedIndex = null;

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
      const extension = file.name.split(".").pop().toLowerCase();
      if (!allowedExtensions.has(extension)) return `${file.name} is not a supported photo type.`;
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
      caption.addEventListener("input", event => { photo.caption = event.target.value; });
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
    updateEstimate();
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
    const hasTitle = document.querySelector("#title").value.trim() || document.querySelector("#subtitle").value.trim();
    const seconds = photos.length * duration + (hasTitle ? 3 : 0);
    videoEstimate.textContent = `Estimated video length: ${formatDuration(seconds)}.`;
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
  document.querySelector("#title").addEventListener("input", updateEstimate);
  document.querySelector("#subtitle").addEventListener("input", updateEstimate);
  function setAdvancedView(enabled) {
    simpleView.setAttribute("aria-pressed", String(!enabled));
    advancedView.setAttribute("aria-pressed", String(enabled));
    customSettings.hidden = !enabled;
    customSettings.open = enabled;
    viewDescription.textContent = enabled
      ? "Advanced view adds transition, movement, font, and caption-position controls."
      : "Simple view keeps the recommended settings and the essential choices.";
  }
  simpleView.addEventListener("click", () => setAdvancedView(false));
  advancedView.addEventListener("click", () => setAdvancedView(true));
  style.addEventListener("change", () => { if (style.value === "custom") setAdvancedView(true); });

  function setProgress(value, message) {
    const rounded = Math.max(0, Math.min(100, Math.round(value)));
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
      background: document.querySelector("#background").value,
      style: style.value,
      transition: document.querySelector("#transition").value,
      movement: document.querySelector("#movement").value,
      font: document.querySelector("#font").value,
      text_position: document.querySelector("#text-position").value,
      rotations: photos.map(photo => photo.rotation),
      captions: photos.map(photo => photo.caption)
    };
  }

  function pollJob(jobId, token = "") {
    let networkFailures = 0;
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
        const messages = { queued: "Waiting for the video maker", preparing: "Preparing your photos", rendering: "Creating the video", ready: "Finished", downloaded: "Finished" };
        setProgress(data.progress, messages[data.state] || "Working");
        if (data.state === "ready" || data.state === "downloaded") {
          progressPanel.hidden = true;
          resultPanel.hidden = false;
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

  form.addEventListener("submit", event => {
    event.preventDefault();
    showError("");
    if (!photos.length) return showError("Choose at least one photo.");
    const settings = settingsPayload();
    if (!Number.isFinite(settings.duration) || settings.duration < 1 || settings.duration > 20) return showError("Photo time must be between 1 and 20 seconds.");
    const estimated = photos.length * settings.duration + (settings.title || settings.subtitle ? 3 : 0);
    if (estimated > 1200) return showError("This slideshow would be longer than 20 minutes. Use fewer photos or a shorter time.");
    const body = new FormData();
    photos.forEach(photo => body.append("files", photo.file, photo.file.name));
    body.append("settings", JSON.stringify(settings));
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs");
    xhr.responseType = "json";
    xhr.timeout = 10 * 60 * 1000;
    xhr.upload.addEventListener("progress", progress => {
      if (progress.lengthComputable) setProgress((progress.loaded / progress.total) * 10, "Uploading your photos");
    });
    xhr.addEventListener("load", () => {
      if (xhr.status < 200 || xhr.status >= 300) {
        progressPanel.hidden = true; form.hidden = false; submit.disabled = false;
        return showError(xhr.response?.detail || xhr.response?.error || "The upload could not be started.");
      }
      setProgress(10, "Upload complete. Preparing your photos");
      const reference = document.querySelector("#job-reference");
      reference.textContent = `Job reference: ${xhr.response.job_id}`;
      reference.hidden = false;
      rememberActiveJob(xhr.response.job_id);
      pollJob(xhr.response.job_id, xhr.response.access_token);
    });
    xhr.addEventListener("error", () => { progressPanel.hidden = true; form.hidden = false; submit.disabled = false; showError("The upload was interrupted. Please try again."); });
    xhr.addEventListener("timeout", () => { progressPanel.hidden = true; form.hidden = false; submit.disabled = false; showError("The upload took too long. Check your connection and try fewer or smaller photos."); });
    submit.disabled = true;
    form.hidden = true;
    progressPanel.hidden = false;
    progressPanel.scrollIntoView({ behavior: "smooth", block: "center" });
    xhr.send(body);
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
  }
})();
