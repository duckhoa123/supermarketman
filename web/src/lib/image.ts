/** Shrink a phone photo before upload: invoices stay readable at 2000 px, and uploads stay small on mobile data. */
export async function prepareUpload(file: File): Promise<{ blob: Blob; name: string }> {
  if (!file.type.startsWith("image/") || file.type === "image/gif") return { blob: file, name: file.name };
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, 2000 / Math.max(bitmap.width, bitmap.height));
    if (scale === 1 && file.size < 1_500_000 && file.type === "image/jpeg") return { blob: file, name: file.name };
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext("2d")?.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
    return blob ? { blob, name: file.name.replace(/\.[^.]+$/, "") + ".jpg" } : { blob: file, name: file.name };
  } catch {
    return { blob: file, name: file.name }; // the browser couldn't decode it; let the server decide
  }
}
