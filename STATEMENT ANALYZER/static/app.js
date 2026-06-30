const input = document.getElementById('statement');
const label = document.getElementById('file-label');
const form = document.getElementById('upload-form');
const button = document.getElementById('analyze-btn');
const trainForm = document.getElementById('train-form');
const trainButton = document.getElementById('train-btn');
const consolidationInput = document.getElementById('consolidation-statements');
const consolidationLabel = document.getElementById('consolidation-file-label');
const consolidationForm = document.getElementById('consolidate-form');
const consolidationButton = document.getElementById('consolidate-btn');
const reviewForm = document.querySelector('.review-form');
const reviewButton = reviewForm ? reviewForm.querySelector('button[type="submit"]') : null;
const adaptiveReviewForm = document.querySelector('.adaptive-review-form');
const adaptiveReviewButton = adaptiveReviewForm ? adaptiveReviewForm.querySelector('button[type="submit"]') : null;
const uploadZones = Array.from(document.querySelectorAll('.upload-zone'));
const jobStatus = document.querySelector('[data-job-status]');
const reviewSearch = document.querySelector('[data-review-search]');
const reviewDirection = document.querySelector('[data-review-direction]');
const reviewStatus = document.querySelector('[data-review-status]');
const reviewRows = Array.from(document.querySelectorAll('[data-review-row]'));
const reviewSelects = Array.from(document.querySelectorAll('[data-review-select]'));
const reviewCounter = document.querySelector('[data-review-selected-count]');
const adaptiveRoleSelects = Array.from(document.querySelectorAll('[data-adaptive-role]'));
const adaptiveChangeCounter = document.querySelector('[data-adaptive-change-count]');
const fileInputs = Array.from(document.querySelectorAll('[data-file-input]'));

const updateReviewCounter = () => {
  const changedCount = reviewSelects.filter((select) => select.value !== (select.dataset.original || '')).length;
  if (reviewCounter) {
    reviewCounter.textContent = changedCount === 1 ? '1 change' : `${changedCount} changes`;
  }
  reviewSelects.forEach((select) => {
    const row = select.closest('tr');
    if (!row) {
      return;
    }
    row.classList.toggle('row-edited', select.value !== (select.dataset.original || ''));
  });
};

const applyReviewFilters = () => {
  const searchValue = (reviewSearch ? reviewSearch.value : '').trim().toLowerCase();
  const directionValue = reviewDirection ? reviewDirection.value : 'all';
  const statusValue = reviewStatus ? reviewStatus.value : 'all';

  reviewRows.forEach((row) => {
    const matchesSearch = !searchValue || (row.dataset.description || '').includes(searchValue);
    const matchesDirection = directionValue === 'all' || row.dataset.direction === directionValue;
    const matchesStatus = statusValue === 'all' || row.dataset.status === statusValue;
    row.hidden = !(matchesSearch && matchesDirection && matchesStatus);
  });
};

const updateAdaptiveCounter = () => {
  const changedCount = adaptiveRoleSelects.filter((select) => select.value !== (select.dataset.original || '')).length;
  if (adaptiveChangeCounter) {
    adaptiveChangeCounter.textContent = changedCount === 1 ? '1 column change' : `${changedCount} column changes`;
  }
  adaptiveRoleSelects.forEach((select) => {
    const card = select.closest('[data-adaptive-column]');
    if (!card) {
      return;
    }
    card.classList.toggle('card-edited', select.value !== (select.dataset.original || ''));
  });
};

if (input && label) {
  input.addEventListener('change', () => {
    if (input.files && input.files[0]) {
      label.textContent = input.files[0].name;
    }
  });
}

fileInputs.forEach((fileInput) => {
  fileInput.addEventListener('change', () => {
    const target = document.getElementById(fileInput.dataset.fileLabelTarget || '');
    if (target && fileInput.files && fileInput.files[0]) {
      target.textContent = fileInput.files[0].name;
    }
  });
});

if (form && button) {
  form.addEventListener('submit', () => {
    button.textContent = 'Analyzing...';
    button.disabled = true;
  });
}

if (trainForm && trainButton) {
  trainForm.addEventListener('submit', () => {
    trainButton.textContent = 'Training...';
    trainButton.disabled = true;
  });
}

if (consolidationInput && consolidationLabel) {
  consolidationInput.addEventListener('change', () => {
    const files = Array.from(consolidationInput.files || []);
    if (files.length === 1) {
      consolidationLabel.textContent = files[0].name;
      return;
    }
    if (files.length > 1) {
      consolidationLabel.textContent = `${files.length} workbooks selected`;
    }
  });
}

if (consolidationForm && consolidationButton) {
  consolidationForm.addEventListener('submit', () => {
    consolidationButton.textContent = 'Consolidating...';
    consolidationButton.disabled = true;
  });
}

if (reviewForm && reviewButton) {
  reviewForm.addEventListener('submit', () => {
    reviewButton.textContent = 'Queued...';
    reviewButton.disabled = true;
  });
  reviewSelects.forEach((select) => {
    select.addEventListener('change', updateReviewCounter);
  });
  document.querySelectorAll('[data-use-suggestion]').forEach((buttonElement) => {
    buttonElement.addEventListener('click', () => {
      const targetName = buttonElement.dataset.target;
      const targetValue = buttonElement.dataset.value || '';
      const targetSelect = reviewForm.querySelector(`[name="${targetName}"]`);
      if (!targetSelect) {
        return;
      }
      targetSelect.value = targetValue;
      updateReviewCounter();
    });
  });
  if (reviewSearch) {
    reviewSearch.addEventListener('input', applyReviewFilters);
  }
  if (reviewDirection) {
    reviewDirection.addEventListener('change', applyReviewFilters);
  }
  if (reviewStatus) {
    reviewStatus.addEventListener('change', applyReviewFilters);
  }
  updateReviewCounter();
  applyReviewFilters();
}

if (adaptiveReviewForm && adaptiveReviewButton) {
  adaptiveReviewForm.addEventListener('submit', () => {
    adaptiveReviewButton.textContent = 'Continuing...';
    adaptiveReviewButton.disabled = true;
  });
  adaptiveRoleSelects.forEach((select) => {
    select.addEventListener('change', updateAdaptiveCounter);
  });
  updateAdaptiveCounter();
}

if (uploadZones.length) {
  uploadZones.forEach((uploadZone) => {
    ['dragenter', 'dragover'].forEach((eventName) => {
      uploadZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        uploadZone.classList.add('is-dragging');
      });
    });

    ['dragleave', 'drop'].forEach((eventName) => {
      uploadZone.addEventListener(eventName, () => {
        uploadZone.classList.remove('is-dragging');
      });
    });
  });
}

if (jobStatus) {
  const jobId = jobStatus.dataset.jobStatus;
  const resultUrl = jobStatus.dataset.resultUrl;
  const statusLabel = jobStatus.querySelector('strong');
  let failedPolls = 0;

  const pollJob = async () => {
    try {
      const response = await fetch(`/status/${jobId}`, { headers: { Accept: 'application/json' } });
      if (!response.ok) {
        failedPolls += 1;
        if (statusLabel) {
          statusLabel.textContent = failedPolls > 2 ? 'Status: reconnecting...' : 'Status: checking...';
        }
        return;
      }

      const payload = await response.json();
      failedPolls = 0;
      if (statusLabel && payload.status) {
        statusLabel.textContent = `Status: ${payload.status}`;
      }

      if (payload.status === 'completed' || payload.status === 'failed' || payload.status === 'review_required') {
        window.location.href = payload.result_url || resultUrl;
      }
    } catch (error) {
      failedPolls += 1;
      if (statusLabel) {
        statusLabel.textContent = failedPolls > 2 ? 'Status: reconnecting...' : 'Status: checking...';
      }
    }
  };

  const pollHandle = window.setInterval(pollJob, 2000);
  pollJob();
}
