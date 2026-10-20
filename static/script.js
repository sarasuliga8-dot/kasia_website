
 // Mobile navigation toggle
const navToggle = document.getElementById('navToggle');
const navLinks = document.getElementById('navLinks');

navToggle.addEventListener('click', () => {
  const isOpen = navLinks.classList.toggle('open');
  navToggle.setAttribute('aria-expanded', String(isOpen));
});

navLinks.querySelectorAll('a').forEach(link => {
  link.addEventListener('click', () => {
    navLinks.classList.remove('open');
    navToggle.setAttribute('aria-expanded', 'false');
  });
});

// Scrollspy: highlight active tab based on section in view
const sections = ['about-me', 'working-together', 'contact-me']
  .map(id => document.getElementById(id))
  .filter(Boolean);

const navItems = document.querySelectorAll('.nav-links a[data-nav]');
const nav = document.querySelector('.site-nav');

const getNavHeight = () => nav ? nav.offsetHeight : 0;

const setActive = (id) => {
  navItems.forEach(link => {
    link.classList.toggle(
      'active',
      link.getAttribute('href') === '#' + id
    );
  });
};

const updateActiveNav = () => {
  if (sections.length === 0) return;

  const triggerLine = window.scrollY + getNavHeight() + 1;
  const atBottom =
    window.innerHeight + window.scrollY >=
    document.documentElement.scrollHeight - 2;

  let current = null;

  for (const section of sections) {
    if (section.offsetTop <= triggerLine) {
      current = section;
    }
  }

  if (atBottom) {
    current = sections[sections.length - 1];
  }

  if (current) {
    setActive(current.id);
  }
};

let ticking = false;

window.addEventListener('scroll', () => {
  if (!ticking) {
    window.requestAnimationFrame(() => {
      updateActiveNav();
      ticking = false;
    });

    ticking = true;
  }
});

window.addEventListener('resize', updateActiveNav);

updateActiveNav();

// Expandable quotes ("Read more" / "Read less")
document.querySelectorAll('.read-more-btn').forEach(button => {
  const quote = button.previousElementSibling;
  const more = quote?.querySelector('.quote-more');

  if (!more) return;

  button.addEventListener('click', () => {
    const expanded = !more.hidden;

    more.hidden = expanded;
    button.textContent = expanded ? 'Read more' : 'Read less';
  });
});

// Contact form: all fields required, email must be well-formed
const contactForm = document.getElementById('contactForm');
if (contactForm) {
  const nameInput = document.getElementById('contactName');
  const emailInput = document.getElementById('contactEmail');
  const messageInput = document.getElementById('contactMessage');
  const emailError = document.getElementById('emailError');
  const submitBtn = document.getElementById('contactSubmit');
  const thanks = document.getElementById('contactThanks');

  const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
  const isEmailValid = () => emailPattern.test(emailInput.value.trim());

  // When the Turnstile widget is on the page, the visitor must complete it first
  const turnstileWidget = document.getElementById('turnstileWidget');
  const isTurnstileComplete = () => !turnstileWidget || getTurnstileToken() !== '';

  const isFormValid = () =>
    nameInput.value.trim() !== '' &&
    messageInput.value.trim() !== '' &&
    isEmailValid() &&
    isTurnstileComplete();

  const showEmailError = () => {
    const invalid = emailInput.value.trim() !== '' && !isEmailValid();
    emailError.hidden = !invalid;
    emailInput.classList.toggle('invalid', invalid);
  };

  const updateSubmit = () => { submitBtn.disabled = !isFormValid(); };

  // The Turnstile callbacks in contact.html fire this when the check is completed or expires
  window.addEventListener('turnstile-state-changed', updateSubmit);

  [nameInput, emailInput, messageInput].forEach(input => {
    input.addEventListener('input', updateSubmit);
  });
  // Show the email hint once the user leaves the field, then live while they fix it
  emailInput.addEventListener('blur', showEmailError);
  emailInput.addEventListener('input', () => {
    if (!emailError.hidden) showEmailError();
  });

  const contactError = document.getElementById('contactError');
  const csrfMeta = document.querySelector('meta[name="csrf-token"]');

  // Turnstile token is injected by the widget as a hidden field inside the form
  const getTurnstileToken = () => {
    const field = contactForm.querySelector('[name="cf-turnstile-response"]');
    return field ? field.value : '';
  };

  const resetTurnstile = () => {
    if (window.turnstile && window.turnstileWidgetId !== undefined) {
      turnstile.reset(window.turnstileWidgetId);
    }
  };

  contactForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!isFormValid()) {
      showEmailError();
      return;
    }

    submitBtn.disabled = true;
    contactError.hidden = true;
    try {
      const response = await fetch(contactForm.action || window.location.pathname, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfMeta ? csrfMeta.content : '',
        },
        body: JSON.stringify({
          name: nameInput.value.trim(),
          email: emailInput.value.trim(),
          message: messageInput.value.trim(),
          'cf-turnstile-response': getTurnstileToken(),
        }),
      });

      if (!response.ok) {
        let data = {};
        try { data = await response.json(); } catch (parseErr) { /* non-JSON error page */ }
        const error = new Error('Request failed');
        error.serverMessage = data.error || '';
        error.retryTurnstile = Boolean(data.retry_turnstile);
        throw error;
      }
      contactForm.hidden = true;
      thanks.hidden = false;
    } catch (err) {
      // Show only messages the server wrote for the visitor, never raw browser errors
      contactError.textContent = err.serverMessage
        || 'Sorry, your message could not be sent. Please try again.';
      contactError.hidden = false;
      // Tokens are single-use, so the visitor must complete the check again
      resetTurnstile();
      updateSubmit();
    }
  });
}
