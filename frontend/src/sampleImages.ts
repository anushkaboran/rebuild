export type SampleImage = {
  src: string
  alt: string
  photographer: string
  source: string
}

const armchair: SampleImage = {
  src: '/sample-images/armchair.jpg',
  alt: 'Illustrative sample photo of a brown leather chair beside a window',
  photographer: 'Stefen Tan',
  source: 'https://unsplash.com/photos/V4wfwY863sI',
}

const lights: SampleImage = {
  src: '/sample-images/pendant-lights.jpg',
  alt: 'Illustrative sample photo of hanging pendant lights',
  photographer: 'Luiz Felipe',
  source: 'https://unsplash.com/photos/p1MrRrw5sd4',
}

const frames: SampleImage = {
  src: '/sample-images/framed-art.jpg',
  alt: 'Illustrative sample photo of framed artwork on a wall',
  photographer: 'Andrew Neel',
  source: 'https://unsplash.com/photos/acowe0pCVBg',
}

const vase: SampleImage = {
  src: '/sample-images/vase.jpg',
  alt: 'Illustrative sample photo of a small glass vase with dried flowers',
  photographer: 'Dmitry Spravko',
  source: 'https://unsplash.com/photos/MqfN_9gc_oQ',
}

// Display-only demo artwork. These images are never used as case evidence.
export const sampleImages: Record<string, SampleImage> = {
  itm_001: armchair,
  itm_002: lights,
  itm_003: lights,
  itm_004: frames,
  itm_005: frames,
  itm_008: vase,
}
