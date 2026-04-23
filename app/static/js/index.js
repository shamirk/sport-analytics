async function handleLookup(e) {
  e.preventDefault();
  const raw = document.getElementById('memberInput').value.trim().toUpperCase();

  if (!raw) {
    showError('Please enter a member number.');
    return;
  }
  if (!/^[A-Z0-9]{5,10}$/.test(raw)) {
    showError('Member number must be 5\u201310 alphanumeric characters.');
    return;
  }

  hideError();
  showLoading('Initiating scrape\u2026');

  try {
    const resp = await fetch(`/api/analyze/${raw}`, { method: 'POST' });
    const body = await resp.json();

    if (!resp.ok) {
      throw new Error(body.detail || 'Scraping failed');
    }

    if (body.status === 'complete') {
      window.location.href = `/dashboard/${raw}`;
      return;
    }

    const jobId = body.job_id;
    updateLoadingText('Scraping USPSA data\u2026');
    await pollJob(raw, jobId);

  } catch (err) {
    hideLoading();
    showError(err.message || 'An unexpected error occurred.');
  }
}

async function pollJob(memberNumber, jobId) {
  const maxAttempts = 60;
  let attempts = 0;

  while (attempts < maxAttempts) {
    await sleep(2000);
    attempts++;

    try {
      const resp = await fetch(`/api/member/${memberNumber}/status`);
      const body = await resp.json();

      if (body.status === 'complete') {
        updateLoadingText('Done! Loading dashboard\u2026');
        window.location.href = `/dashboard/${memberNumber}`;
        return;
      }

      if (body.status === 'error') {
        hideLoading();
        showError(body.error || 'Scraping failed. Please try again.');
        return;
      }

      const eta = Math.max(0, (maxAttempts - attempts) * 2);
      updateLoadingText(`Scraping in progress\u2026 (~${eta}s remaining)`);

    } catch (fetchErr) {
      // transient fetch error — continue polling
    }
  }

  hideLoading();
  showError('Timed out waiting for data. Please try again.');
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function showError(msg) {
  const el = document.getElementById('errorBanner');
  el.textContent = msg;
  el.classList.add('visible');
}
function hideError() { document.getElementById('errorBanner').classList.remove('visible'); }
function showLoading(msg) {
  document.getElementById('loadingText').textContent = msg;
  document.getElementById('loadingOverlay').classList.add('visible');
}
function updateLoadingText(msg) { document.getElementById('loadingText').textContent = msg; }
function hideLoading() { document.getElementById('loadingOverlay').classList.remove('visible'); }

document.getElementById('lookupForm').addEventListener('submit', handleLookup);
