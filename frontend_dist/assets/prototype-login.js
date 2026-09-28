import { api } from './common.js';

const PRIVACY_POLICY_VERSION = '2026-09-28-central-chat-v1';
const USAGE_RULES_VERSION = '1.0';
const sessionReady = api('/api/auth/me/');
sessionReady.then(session => {
  if (!session.authenticated) return;
  if (session.policy_consent_required) {
    document.querySelector('.tabs').hidden = true;
    document.querySelectorAll('.form').forEach(form => { form.classList.remove('active'); form.hidden = true; });
    document.getElementById('policyUpdateRequired').hidden = false;
    return;
  }
  location.assign('/');
}).catch(() => {});

async function submit(mode) {
  const panel = document.getElementById(mode);
  const fields = panel.querySelectorAll('input');
  const username = fields[0].value.trim();
  const password = fields[1].value;
  const error = document.getElementById(`${mode}Error`);
  const button = document.getElementById(`${mode}Submit`);
  error.hidden = true;
  if (!username || !password) { error.textContent = '请输入账号和密码'; error.hidden = false; return; }
  if (mode === 'register' && password !== fields[2].value) {
    error.textContent = '两次输入的密码不一致'; error.hidden = false; return;
  }
  const consent = document.getElementById('policyConsent');
  if (mode === 'register' && !consent.checked) {
    error.textContent = '请先阅读并同意隐私协议和用户使用规则'; error.hidden = false; return;
  }
  button.disabled = true;
  try {
    await sessionReady;
    const body = mode === 'register'
      ? { username, password, policy_consent: true, privacy_policy_version: PRIVACY_POLICY_VERSION, usage_rules_version: USAGE_RULES_VERSION }
      : { username, password, remember: fields[2].checked };
    await api(`/api/auth/${mode === 'register' ? 'register' : 'login'}/`, { method: 'POST', body, redirectOnUnauthorized: false });
    location.assign('/');
  } catch (failure) {
    error.textContent = failure.message;
    error.hidden = false;
    button.disabled = false;
  }
}

document.getElementById('loginSubmit').addEventListener('click', () => submit('login'));
document.getElementById('registerSubmit').addEventListener('click', () => submit('register'));
const policyConsent = document.getElementById('policyConsent');
policyConsent.addEventListener('change', () => { document.getElementById('registerSubmit').disabled = !policyConsent.checked; });
document.querySelectorAll('[data-policy-dialog]').forEach(button => {
  button.addEventListener('click', () => document.getElementById(button.dataset.policyDialog).showModal());
});
document.querySelectorAll('[data-close-dialog]').forEach(button => {
  button.addEventListener('click', () => button.closest('dialog').close());
});
const updateConsent = document.getElementById('policyUpdateConsent');
const updateSubmit = document.getElementById('policyUpdateSubmit');
updateConsent.addEventListener('change', () => { updateSubmit.disabled = !updateConsent.checked; });
updateSubmit.addEventListener('click', async () => {
  if (!updateConsent.checked) return;
  updateSubmit.disabled = true;
  const error = document.getElementById('policyUpdateError');
  error.hidden = true;
  try {
    await sessionReady;
    await api('/api/auth/consent/', {
      method: 'POST',
      body: { policy_consent: true, privacy_policy_version: PRIVACY_POLICY_VERSION, usage_rules_version: USAGE_RULES_VERSION },
    });
    location.assign('/app/');
  } catch (failure) {
    error.textContent = failure.message;
    error.hidden = false;
    updateSubmit.disabled = !updateConsent.checked;
  }
});
document.getElementById('policyUpdateLogout').addEventListener('click', async () => {
  await api('/api/auth/logout/', { method: 'POST', body: {} });
  location.assign('/login/');
});
document.querySelectorAll('.form input').forEach(input => input.addEventListener('keydown', event => {
  if (event.key === 'Enter') { event.preventDefault(); submit(input.closest('.form').id); }
}));

