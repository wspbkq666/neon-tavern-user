import { api } from './common.js';

const tabs = { login: document.getElementById('login-tab'), register: document.getElementById('register-tab') };
const form = document.getElementById('auth-form');
const error = document.getElementById('auth-error');
let mode = 'login';
const sessionReady = api('/api/auth/me/', { redirectOnUnauthorized: false });

function setMode(next) {
  mode = next;
  for (const [name, button] of Object.entries(tabs)) {
    button.classList.toggle('active', name === mode);
    button.setAttribute('aria-selected', String(name === mode));
  }
  document.getElementById('confirm-field').classList.toggle('hidden', mode !== 'register');
  document.getElementById('confirm-password').required = mode === 'register';
  document.getElementById('password').autocomplete = mode === 'register' ? 'new-password' : 'current-password';
  document.getElementById('submit-auth').textContent = mode === 'register' ? '创建账号' : '登录';
  error.hidden = true;
}

tabs.login.addEventListener('click', () => setMode('login'));
tabs.register.addEventListener('click', () => setMode('register'));
document.getElementById('show-password').addEventListener('click', event => {
  const password = document.getElementById('password');
  const visible = password.type === 'password';
  password.type = visible ? 'text' : 'password';
  event.currentTarget.textContent = visible ? '隐藏' : '显示';
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  error.hidden = true;
  if (!form.reportValidity()) return;
  const username = document.getElementById('username').value.trim();
  const password = document.getElementById('password').value;
  if (username.length < 3 || username.length > 30) {
    error.textContent = '账号需要 3 至 30 个字符'; error.hidden = false; return;
  }
  if (mode === 'register' && password !== document.getElementById('confirm-password').value) {
    error.textContent = '两次输入的密码不一致'; error.hidden = false; return;
  }
  const submit = document.getElementById('submit-auth');
  submit.disabled = true;
  try {
    await sessionReady;
    await api(`/api/auth/${mode === 'register' ? 'register' : 'login'}/`, { method: 'POST', body: { username, password }, redirectOnUnauthorized: false });
    window.location.href = '/';
  } catch (failure) {
    error.textContent = failure.message; error.hidden = false;
    submit.disabled = false;
  }
});

sessionReady.then(session => {
  if (session.authenticated) window.location.href = '/';
}).catch(() => {});
