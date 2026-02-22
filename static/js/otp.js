/**
 * OTP input handler — 4 separated boxes with blinking caret,
 * auto-advance, and auto-submit on completion.
 */
(function () {
  const input = document.getElementById('otp-input');
  if (!input || input.disabled) return;

  const hidden = document.getElementById('otp-hidden');
  const group = document.getElementById('otp-group');
  const slots = group.querySelectorAll('.otp-slot');
  const btn = document.getElementById('otp-btn');
  const form = document.getElementById('otp-form');

  function update() {
    const val = input.value;
    hidden.value = val;
    slots.forEach((s, i) => {
      const char = s.querySelector('.otp-char');
      char.textContent = val[i] || '';
      s.classList.toggle('filled', !!val[i]);
      s.classList.toggle('active', i === val.length && document.activeElement === input);
    });
    btn.disabled = val.length < 4;
  }

  input.addEventListener('input', function () {
    this.value = this.value.replace(/\D/g, '').slice(0, 4);
    update();
    if (this.value.length === 4) {
      hidden.value = this.value;
      btn.textContent = 'Verifying\u2026';
      btn.disabled = true;
      form.submit();
    }
  });

  input.addEventListener('focus', update);
  input.addEventListener('blur', function () {
    slots.forEach(s => s.classList.remove('active'));
  });

  group.addEventListener('click', function () {
    input.focus();
  });

  update();
  input.focus();
})();
