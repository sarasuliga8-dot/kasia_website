// Mobile nav toggle
const navToggle = document.getElementById('navToggle');
const navLinks = document.getElementById('navLinks');
navToggle.addEventListener('click', () => navLinks.classList.toggle('open'));
navLinks.querySelectorAll('a').forEach(link => {
  link.addEventListener('click', () => navLinks.classList.remove('open'));
});

// Scrollspy: highlight active tab based on section in view
const sections = ['about-me', 'working-together', 'contact-me']
  .map(id => document.getElementById(id))
  .filter(Boolean);
const navItems = document.querySelectorAll('.nav-links a[data-nav]');
const navHeight = document.querySelector('.site-nav').offsetHeight;

const setActive = (id) => {
  navItems.forEach(a => {
    a.classList.toggle('active', a.getAttribute('href') === '#' + id);
  });
};

const updateActiveNav = () => {
  const triggerLine = window.scrollY + navHeight + 1;
  const atBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;

  let current = null;
  for (const sec of sections) {
    if (sec.offsetTop <= triggerLine) current = sec;
  }
  if (atBottom) current = sections[sections.length - 1];

  if (current) setActive(current.id);
};

let ticking = false;
window.addEventListener('scroll', () => {
  if (!ticking) {
    requestAnimationFrame(() => {
      updateActiveNav();
      ticking = false;
    });
    ticking = true;
  }
});
window.addEventListener('resize', updateActiveNav);
updateActiveNav();
