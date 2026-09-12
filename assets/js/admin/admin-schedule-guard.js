(() => {
  const root = typeof window !== 'undefined' ? window : globalThis;

  function minutes(value) {
    if (!/^\d{2}:\d{2}$/.test(String(value || ''))) return null;
    const [hour, minute] = String(value).split(':').map(Number);
    if (!Number.isInteger(hour) || !Number.isInteger(minute) || hour > 23 || minute > 59) return null;
    return hour * 60 + minute;
  }

  function itemKey(workflowId, cron) {
    return `${workflowId}\n${cron}`;
  }

  function validateScheduleChange(items, workflowId, cron, time, currentTimes = {}) {
    const item = (items || []).find((candidate) => itemKey(candidate.workflow_id, candidate.cron) === itemKey(workflowId, cron));
    if (!item || time === item.kst_time) return null;
    const proposed = minutes(time);
    if (proposed == null) {
      return { title: '실행 시간을 변경할 수 없습니다', message: '올바른 한국 표준시를 입력해 주세요.' };
    }

    const earliest = minutes(item.earliest_safe_time_kst);
    if (earliest != null && proposed < earliest) {
      const reason = item.dependency_reason || '이 작업의 원천데이터가 아직 확정되지 않은 시각입니다.';
      return {
        title: '실행 시간을 변경할 수 없습니다',
        message: `${reason} ${item.earliest_safe_time_kst} KST 이후 시간으로만 변경할 수 있습니다.`,
      };
    }

    const byId = new Map((items || []).map((candidate) => [candidate.workflow_id, candidate]));
    const displayedTime = (id) => currentTimes[id] || byId.get(id)?.kst_time || null;
    if (item.must_run_before_id) {
      const other = byId.get(item.must_run_before_id);
      const otherTime = displayedTime(item.must_run_before_id);
      const otherMinutes = minutes(otherTime);
      const end = proposed + Math.max(0, Number(item.retry_minutes) || 0);
      if (other && otherMinutes != null && end >= otherMinutes) {
        const retryText = Number(item.retry_minutes) > 0 ? '재시도까지 ' : '';
        return {
          title: '실행 시간을 변경할 수 없습니다',
          message: `${item.name}의 ${retryText}${other.name}(${otherTime} KST) 이전에 끝나도록 설정해야 합니다.`,
        };
      }
    }
    if (item.must_run_after_id) {
      const other = byId.get(item.must_run_after_id);
      const otherTime = displayedTime(item.must_run_after_id);
      const otherMinutes = minutes(otherTime);
      const otherEnd = otherMinutes == null ? null : otherMinutes + Math.max(0, Number(other?.retry_minutes) || 0);
      if (other && otherEnd != null && proposed <= otherEnd) {
        const retryText = Number(other.retry_minutes) > 0 ? '재시도 ' : '';
        return {
          title: '실행 시간을 변경할 수 없습니다',
          message: `${item.name}은 ${other.name} ${retryText}완료(${otherTime} KST 기준) 이후 시간으로만 변경할 수 있습니다.`,
        };
      }
    }
    return null;
  }

  root.MacroWatchScheduleGuard = Object.freeze({ validateScheduleChange });
  if (typeof document === 'undefined') return;

  const list = document.getElementById('automation-schedule-list');
  if (!list) return;
  let items = [];
  let refreshTimer = null;

  function annotateRows() {
    const byKey = new Map(items.map((item) => [itemKey(item.workflow_id, item.cron), item]));
    list.querySelectorAll('[data-automation-workflow-id]').forEach((row) => {
      const item = byKey.get(itemKey(row.dataset.automationWorkflowId, row.dataset.automationCron));
      if (!item) return;
      const input = row.querySelector('[data-automation-time]');
      if (input && item.earliest_safe_time_kst) input.min = item.earliest_safe_time_kst;
      const deleteButton = row.querySelector('[data-delete-automation-time]');
      if (deleteButton && item.deletable === false) deleteButton.classList.add('hidden');
    });
  }

  async function refreshMetadata() {
    try {
      const api = root.MacroWatchAdminApi;
      if (!api?.invoke) return;
      const result = await api.invoke('list_automation_schedules');
      items = Array.isArray(result?.items) ? result.items : [];
      annotateRows();
    } catch (_) {
      // The existing admin loader remains authoritative for visible errors.
      // Backend validation still protects schedule writes if metadata refresh fails.
    }
  }

  function scheduleMetadataRefresh() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(refreshMetadata, 50);
  }

  list.addEventListener('click', (event) => {
    const button = event.target.closest?.('[data-save-automation]');
    if (!button) return;
    const row = button.closest('[data-automation-workflow-id]');
    if (!row) return;
    const currentTimes = {};
    list.querySelectorAll('[data-automation-workflow-id]').forEach((candidate) => {
      const input = candidate.querySelector('[data-automation-time]');
      if (input) currentTimes[candidate.dataset.automationWorkflowId] = input.value;
    });
    const input = row.querySelector('[data-automation-time]');
    const issue = validateScheduleChange(
      items,
      row.dataset.automationWorkflowId,
      row.dataset.automationCron,
      input?.value || '',
      currentTimes,
    );
    if (!issue) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    root.MacroWatchAdminApi?.notice?.(issue.title, issue.message, true);
  }, true);

  new MutationObserver(scheduleMetadataRefresh).observe(list, { childList: true, subtree: true });
  scheduleMetadataRefresh();
})();
