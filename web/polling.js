const Polling = (() => {
  const ACTIVE_STATUSES = new Set(['checking_cache', 'running', 'cancelling']);

  async function pollStatus(url, options = {}) {
    const pollMs = options.pollMs ?? 2000;
    const maxFailures = options.maxConsecutiveFailures ?? 5;
    const fetchImpl = options.fetchImpl ?? fetch;
    const sleep = options.sleep ?? (ms => new Promise(resolve => setTimeout(resolve, ms)));
    let consecutiveFailures = 0;

    for (;;) {
      const backoff = Math.min(4, 2 ** consecutiveFailures);
      await sleep(pollMs * backoff);
      let status;
      try {
        const response = await fetchImpl(url, { cache: 'no-store' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        status = await response.json();
      } catch (_error) {
        consecutiveFailures += 1;
        if (consecutiveFailures >= maxFailures) {
          throw new Error(
            'servern svarar inte; jobbet kan fortfarande köras — ' +
            'kontrollera servern och försök sedan återansluta');
        }
        continue;
      }
      consecutiveFailures = 0;
      if (ACTIVE_STATUSES.has(status.status)) {
        options.onProgress?.(status);
        continue;
      }
      return status;
    }
  }

  return { pollStatus };
})();
