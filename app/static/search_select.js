/* 曲目下拉搜索过滤：输入关键字即时筛选，并显示匹配/已选数量。 */
function attachSongFilter(inputId, selectId, counterId) {
  const input = document.getElementById(inputId);
  const select = document.getElementById(selectId);
  const counter = document.getElementById(counterId);
  if (!input || !select) return;
  function refresh() {
    const q = input.value.trim().toLowerCase();
    let visible = 0;
    for (const opt of select.options) {
      const show = !q || opt.text.toLowerCase().includes(q);
      opt.style.display = show ? "" : "none";
      if (show) visible++;
    }
    if (counter) {
      counter.textContent = "匹配 " + visible + " 项，已选 " + select.selectedOptions.length + " 项";
    }
  }
  input.addEventListener("input", refresh);
  select.addEventListener("change", refresh);
  refresh();
}
