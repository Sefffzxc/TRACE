/**
 * Every photograph the interface uses, in one place.
 *
 * These are free-to-use stock photos served straight from Unsplash's CDN, so
 * there is nothing to download and nothing to license. Swap any URL below for
 * your own school photo — drop the file into `public/images/` and point the
 * entry at `/images/your-file.jpg`. Nothing else in the codebase needs to
 * change.
 *
 * Every image sits behind a gradient in the UI, so if a URL is ever
 * unreachable the screen still looks finished rather than broken.
 */

export const IMAGES = {
  /* Sign-in screen — the large photo beside the form. A school corridor with
     students works best: wide, calm, and with room for text on the left. */
  loginBackground:
    'https://images.unsplash.com/photo-1523050854058-8df90110c9f1?auto=format&fit=crop&w=1920&q=80',

  /* Fallback shown if the photo above cannot load. */
  loginBackgroundAlt:
    'https://images.unsplash.com/photo-1562774053-701939374585?auto=format&fit=crop&w=1920&q=80',

  /* Narrow banner across the top of the dashboard. */
  dashboardBanner:
    'https://images.unsplash.com/photo-1427504494785-3a9ca7044f45?auto=format&fit=crop&w=1920&q=70',

  /* Header strip on the student profile page. */
  studentProfileBanner:
    'https://images.unsplash.com/photo-1503676260728-1c00da094a0b?auto=format&fit=crop&w=1600&q=70',

  /* Header strip on the class-management page. */
  classroomBanner:
    'https://images.unsplash.com/photo-1580582932707-520aed937b7b?auto=format&fit=crop&w=1600&q=70',

  /* Small illustration on the "registration received" screen. */
  approvalPending:
    'https://images.unsplash.com/photo-1434030216411-0b793f4b4173?auto=format&fit=crop&w=1200&q=70',
};

/** School identity — change these and the whole app follows. */
export const SCHOOL = {
  name: "Lord's Blessing Christian Academy",
  shortName: 'LBCA',
  tagline: 'Student PACE Monitoring & Early Warning System',
  motto: 'Wisdom begins with reverence.',
  address: 'Cabanatuan City, Nueva Ecija',
};
