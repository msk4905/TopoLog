(() => {
  'use strict';
  const data = window.TOPOLOG_DEMO_DATA;
  if (!data || !Array.isArray(data.commands) || !data.commands.length) return;
  const $ = id => document.getElementById(id);
  $('project-name').textContent = data.project;
  $('node-count').textContent = `${data.nodeCount}대`;
  $('collected-node').textContent = data.node;
  $('command-count').textContent = `${data.commands.length}개`;
  $('neighbor-count').textContent = `${data.fullNeighbors}개`;
  $('capture-time').textContent = '실제 수집 · ' + new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Seoul' }).format(new Date(data.capturedAt));
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  function selectTab(button) {
    tabs.forEach(tab => {
      const active = tab === button;
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      $(tab.getAttribute('aria-controls')).hidden = !active;
    });
  }
  tabs.forEach((tab, i) => {
    tab.addEventListener('click', () => selectTab(tab));
    tab.addEventListener('keydown', event => {
      let index;
      if (event.key === 'ArrowRight') index = (i + 1) % tabs.length;
      else if (event.key === 'ArrowLeft') index = (i + tabs.length - 1) % tabs.length;
      else if (event.key === 'Home') index = 0;
      else if (event.key === 'End') index = tabs.length - 1;
      else return;
      event.preventDefault(); selectTab(tabs[index]); tabs[index].focus();
    });
  });
  let selected = 0;
  let textMode = false;
  const commandButtons = [];
  function updateOutput() {
    const command = data.commands[selected];
    $('selected-command').textContent = command.command;
    $('command-image').src = command.image;
    $('command-image').alt = `${data.node} — ${command.command} 실제 출력으로 생성한 이미지`;
    $('command-text').textContent = command.text;
    $('command-text').hidden = !textMode;
    $('command-image').hidden = textMode;
    $('toggle-output').textContent = textMode ? '이미지 보기' : '텍스트 보기';
    $('command-result').textContent = command.cliError ? '수집 완료 · CLI 오류 문자열 탐지 · 확인이 필요합니다.' : '수집 완료 · CLI 오류 문자열 없음 · 실습 동작 판정은 별도입니다.';
    commandButtons.forEach((button, i) => button.setAttribute('aria-pressed', String(i === selected)));
  }
  data.commands.forEach((command, i) => {
    const button = document.createElement('button'); button.type = 'button';
    button.textContent = command.command; button.setAttribute('aria-pressed', String(i === 0));
    button.addEventListener('click', () => { selected = i; updateOutput(); });
    commandButtons.push(button); $('command-list').append(button);
  });
  $('toggle-output').addEventListener('click', () => { textMode = !textMode; updateOutput(); });
  function download(filename, text) {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename;
    document.body.append(anchor); anchor.click(); anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  $('download-output').addEventListener('click', () => download(`${data.node}_${data.commands[selected].command.replaceAll(' ', '_')}.txt`, data.commands[selected].text));
  $('download-prompt').addEventListener('click', () => download('ai_prompt.md', data.prompt));
  updateOutput();
})();
