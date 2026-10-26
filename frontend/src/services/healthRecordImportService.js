/**
 * Health Record Import Service
 * Uploads a C-CDA XML, MyChart XDM zip or FHIR R4 Bundle for preview and import.
 */

import { apiClient } from './apiClient';

class HealthRecordImportService {
  /**
   * Accepted upload formats and the size limit.
   * @returns {Promise<{formats: Array, max_file_size_bytes: number, import_sources: string[]}>}
   */
  async getFormats() {
    const response = await apiClient.get('/health-records/import/formats');
    return response?.data ?? response;
  }

  /**
   * Convert the file and report what an import would create or update.
   * @param {number} patientId
   * @param {File} file
   */
  async previewImport(patientId, file) {
    const formData = new FormData();
    formData.append('patient_id', String(patientId));
    formData.append('file', file);
    const response = await apiClient.postForm(
      '/health-records/import/preview',
      formData
    );
    return response?.data ?? response;
  }

  /**
   * Import the file for the patient as idempotent upserts.
   * @param {number} patientId
   * @param {File} file
   * @param {boolean} updateExisting - false leaves previously imported rows unchanged
   */
  async executeImport(patientId, file, updateExisting = true) {
    const formData = new FormData();
    formData.append('patient_id', String(patientId));
    formData.append('update_existing', String(updateExisting));
    formData.append('file', file);
    const response = await apiClient.postForm(
      '/health-records/import/execute',
      formData
    );
    return response?.data ?? response;
  }
}

export const healthRecordImportService = new HealthRecordImportService();
export default healthRecordImportService;
