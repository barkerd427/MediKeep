/**
 * HealthRecordImportModal - Multi-step modal for importing a health record
 * (C-CDA XML, MyChart XDM zip or FHIR R4 Bundle) into the current patient.
 *
 * Steps:
 * 1. Upload the file
 * 2. Preview: counts per record type, warnings, sections not imported
 * 3. Importing (progress)
 * 4. Complete (summary)
 */
import React, { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Modal,
  FileInput,
  Table,
  Alert,
  Checkbox,
  Button,
  Stack,
  Group,
  Text,
  Loader,
  Center,
  Divider,
  Paper,
  List,
} from '@mantine/core';
import {
  IconFileImport,
  IconAlertTriangle,
  IconCheck,
  IconUpload,
  IconArrowLeft,
} from '@tabler/icons-react';
import { healthRecordImportService } from '../../services/healthRecordImportService';
import { useDateFormat } from '../../hooks/useDateFormat';
import logger from '../../services/logger';

interface HealthRecordImportModalProps {
  opened: boolean;
  onClose: () => void;
  patientId: number | null;
  onImportComplete: () => void;
}

interface ImportFormat {
  key: string;
  name: string;
  extensions: string[];
  description: string;
}

interface FormatsResponse {
  formats: ImportFormat[];
  max_file_size_bytes: number;
  import_sources: string[];
}

interface PreviewCount {
  new: number;
  update: number;
  skipped: number;
}

interface ResultCount {
  created: number;
  updated: number;
  skipped: number;
}

interface UnmappedGroup {
  resource_type: string;
  reason: string;
  count: number;
  examples: string[];
}

interface SectionReport {
  name: string;
  entries_seen: number;
  resources_produced: number;
  skipped: { reason: string }[];
}

interface PreviewData {
  source_format: string;
  import_source: string;
  document_count: number;
  counts: Record<string, PreviewCount>;
  total_new: number;
  total_update: number;
  total_skipped: number;
  conversion_report: { documents: { sections: SectionReport[] }[] } | null;
  unmapped: UnmappedGroup[];
  warnings: string[];
  patient: {
    full_name: string | null;
    birth_date: string | null;
    gender: string | null;
  } | null;
}

interface ImportResult {
  counts: Record<string, ResultCount>;
  total_created: number;
  total_updated: number;
  total_skipped: number;
  unmapped: UnmappedGroup[];
  warnings: string[];
}

type Step = 'select' | 'preview' | 'importing' | 'complete';

const ENTITY_ORDER = [
  'conditions',
  'allergies',
  'medications',
  'immunizations',
  'procedures',
  'encounters',
  'vitals',
  'lab_results',
  'lab_test_components',
];

const HealthRecordImportModal: React.FC<HealthRecordImportModalProps> = ({
  opened,
  onClose,
  patientId,
  onImportComplete,
}) => {
  const { t } = useTranslation(['reports', 'shared', 'common']);
  const { formatDate } = useDateFormat();

  // State
  const [step, setStep] = useState<Step>('select');
  const [formats, setFormats] = useState<FormatsResponse | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [previewData, setPreviewData] = useState<PreviewData | null>(null);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [updateExisting, setUpdateExisting] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load accepted formats when modal opens
  useEffect(() => {
    if (opened && !formats) {
      healthRecordImportService
        .getFormats()
        .then((response: FormatsResponse) => setFormats(response))
        .catch(() => {
          logger.error('health_record_import_load_formats_failed', {
            description: 'Failed to load health record import formats',
            component: 'HealthRecordImportModal',
          });
        });
    }
  }, [opened, formats]);

  // Reset state when modal closes
  useEffect(() => {
    if (!opened) {
      setStep('select');
      setFile(null);
      setPreviewData(null);
      setImportResult(null);
      setUpdateExisting(true);
      setLoading(false);
      setError(null);
    }
  }, [opened]);

  const handlePreview = useCallback(async () => {
    if (!file) {
      setError(t('reports:healthRecordImport.file.required'));
      return;
    }
    if (!patientId) return;

    setLoading(true);
    setError(null);

    try {
      const result = await healthRecordImportService.previewImport(
        patientId,
        file
      );
      setPreviewData(result);
      setStep('preview');
    } catch (err: any) {
      const message =
        err?.response?.data?.message ||
        err?.response?.data?.detail ||
        err?.data?.message ||
        err?.message ||
        t('reports:healthRecordImport.errors.previewFailed');
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [file, patientId, t]);

  const handleImport = useCallback(async () => {
    if (!file || !patientId) return;

    setStep('importing');
    setError(null);

    try {
      const result = await healthRecordImportService.executeImport(
        patientId,
        file,
        updateExisting
      );
      setImportResult(result);
      setStep('complete');
    } catch (err: any) {
      const message =
        err?.response?.data?.message ||
        err?.response?.data?.detail ||
        err?.data?.message ||
        err?.message ||
        t('reports:healthRecordImport.errors.importFailed');
      setError(message);
      setStep('preview');
    }
  }, [file, patientId, updateExisting, t]);

  const handleComplete = useCallback(() => {
    onImportComplete();
    onClose();
  }, [onImportComplete, onClose]);

  const acceptedExtensions = (formats?.formats ?? [])
    .flatMap(f => f.extensions)
    .join(',');
  const maxSizeMb = formats
    ? Math.round(formats.max_file_size_bytes / (1024 * 1024))
    : null;

  const entityLabel = (key: string) =>
    t(`reports:healthRecordImport.entities.${key}`, key);

  const renderUnmapped = (groups: UnmappedGroup[]) =>
    groups.length > 0 && (
      <Alert
        variant="light"
        color="gray"
        icon={<IconAlertTriangle size={16} />}
      >
        <Text size="sm" fw={500} mb="xs">
          {t('reports:healthRecordImport.unmapped')}
        </Text>
        <List size="xs" spacing={2}>
          {groups.map((group, i) => (
            <List.Item key={i}>
              {group.resource_type}: {group.reason} (
              {t('reports:healthRecordImport.unmappedCount', {
                count: group.count,
              })}
              )
              {group.examples.length > 0 && (
                <Text span c="dimmed">
                  {' '}
                  {group.examples.join('; ')}
                </Text>
              )}
            </List.Item>
          ))}
        </List>
      </Alert>
    );

  const renderWarnings = (warnings: string[]) =>
    warnings.length > 0 && (
      <Alert
        variant="light"
        color="yellow"
        icon={<IconAlertTriangle size={16} />}
      >
        <Text size="sm" fw={500} mb="xs">
          {t('reports:healthRecordImport.warnings')} ({warnings.length})
        </Text>
        {warnings.slice(0, 5).map((w, i) => (
          <Text key={i} size="xs">
            {w}
          </Text>
        ))}
        {warnings.length > 5 && (
          <Text size="xs" c="dimmed" mt="xs">
            {t('shared:labels.andCountMore', { count: warnings.length - 5 })}
          </Text>
        )}
      </Alert>
    );

  const renderSelectStep = () => (
    <Stack gap="md">
      <Text size="sm" c="dimmed">
        {t('reports:healthRecordImport.description')}
      </Text>

      <FileInput
        label={t('reports:healthRecordImport.file.label')}
        placeholder={t('reports:healthRecordImport.file.placeholder')}
        accept={acceptedExtensions || '.xml,.zip,.json'}
        value={file}
        onChange={setFile}
        leftSection={<IconUpload size={16} />}
      />

      {formats && (
        <Text size="xs" c="dimmed">
          {t('reports:healthRecordImport.acceptedFormats', {
            formats: formats.formats.map(f => f.name).join(', '),
            size: maxSizeMb,
          })}
        </Text>
      )}

      {error && (
        <Alert
          variant="light"
          color="red"
          icon={<IconAlertTriangle size={16} />}
        >
          {error}
        </Alert>
      )}

      <Group justify="flex-end">
        <Button variant="default" onClick={onClose}>
          {t('shared:fields.cancel')}
        </Button>
        <Button
          onClick={handlePreview}
          loading={loading}
          leftSection={<IconFileImport size={16} />}
        >
          {t('reports:healthRecordImport.preview')}
        </Button>
      </Group>
    </Stack>
  );

  const renderPreviewStep = () => {
    if (!previewData) return null;
    const totalToImport = previewData.total_new + previewData.total_update;
    const sections =
      previewData.conversion_report?.documents.flatMap(d => d.sections) ?? [];

    return (
      <Stack gap="md">
        {/* Summary stats */}
        <Paper p="md" withBorder>
          <Group grow>
            <Stack gap={2} align="center">
              <Text size="xl" fw={700} c="green">
                {previewData.total_new}
              </Text>
              <Text size="xs" c="dimmed">
                {t('reports:healthRecordImport.columns.new')}
              </Text>
            </Stack>
            <Stack gap={2} align="center">
              <Text size="xl" fw={700} c="blue">
                {previewData.total_update}
              </Text>
              <Text size="xs" c="dimmed">
                {t('reports:healthRecordImport.columns.update')}
              </Text>
            </Stack>
            <Stack gap={2} align="center">
              <Text size="xl" fw={700} c="orange">
                {previewData.total_skipped}
              </Text>
              <Text size="xs" c="dimmed">
                {t('reports:healthRecordImport.columns.skipped')}
              </Text>
            </Stack>
          </Group>
        </Paper>

        {/* Patient named in the file, for the user to check */}
        {previewData.patient?.full_name && (
          <Text size="sm" c="dimmed">
            {t('reports:healthRecordImport.patientInFile', {
              name: previewData.patient.full_name,
              birthDate: previewData.patient.birth_date
                ? formatDate(previewData.patient.birth_date)
                : '-',
            })}
          </Text>
        )}

        {renderWarnings(previewData.warnings)}

        {totalToImport === 0 && (
          <Alert
            variant="light"
            color="orange"
            icon={<IconAlertTriangle size={16} />}
          >
            {t('reports:healthRecordImport.nothingToImport')}
          </Alert>
        )}

        {/* Counts per record type */}
        <Table striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>
                {t('reports:healthRecordImport.columns.type')}
              </Table.Th>
              <Table.Th>{t('reports:healthRecordImport.columns.new')}</Table.Th>
              <Table.Th>
                {t('reports:healthRecordImport.columns.update')}
              </Table.Th>
              <Table.Th>
                {t('reports:healthRecordImport.columns.skipped')}
              </Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {ENTITY_ORDER.filter(key => previewData.counts[key]).map(key => (
              <Table.Tr key={key}>
                <Table.Td>{entityLabel(key)}</Table.Td>
                <Table.Td>{previewData.counts[key].new}</Table.Td>
                <Table.Td>{previewData.counts[key].update}</Table.Td>
                <Table.Td>{previewData.counts[key].skipped}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>

        {previewData.total_update > 0 && (
          <Checkbox
            label={t('reports:healthRecordImport.updateExisting')}
            description={t('reports:healthRecordImport.updateExistingHint')}
            checked={updateExisting}
            onChange={e => setUpdateExisting(e.currentTarget.checked)}
          />
        )}

        {/* Sections the converter found in the document */}
        {sections.length > 0 && (
          <Stack gap={2}>
            <Text size="sm" fw={500}>
              {t('reports:healthRecordImport.sections')}
            </Text>
            {sections.map((section, i) => (
              <Text key={i} size="xs" c="dimmed">
                {t('reports:healthRecordImport.sectionLine', {
                  name: section.name,
                  entries: section.entries_seen,
                  resources: section.resources_produced,
                  skipped: section.skipped.length,
                })}
              </Text>
            ))}
          </Stack>
        )}

        {renderUnmapped(previewData.unmapped)}

        {error && (
          <Alert
            variant="light"
            color="red"
            icon={<IconAlertTriangle size={16} />}
          >
            {error}
          </Alert>
        )}

        <Divider />

        <Group justify="space-between">
          <Button
            variant="default"
            onClick={() => {
              setStep('select');
              setError(null);
            }}
            leftSection={<IconArrowLeft size={16} />}
          >
            {t('reports:healthRecordImport.back')}
          </Button>
          <Button
            onClick={handleImport}
            disabled={totalToImport === 0}
            leftSection={<IconFileImport size={16} />}
          >
            {t('reports:healthRecordImport.importButton')} (
            {updateExisting ? totalToImport : previewData.total_new})
          </Button>
        </Group>
      </Stack>
    );
  };

  const renderImportingStep = () => (
    <Center py="xl">
      <Stack align="center" gap="md">
        <Loader size="lg" />
        <Text>{t('reports:healthRecordImport.importing')}</Text>
      </Stack>
    </Center>
  );

  const renderCompleteStep = () => {
    if (!importResult) return null;
    return (
      <Stack gap="md" align="center" py="md">
        <IconCheck size={48} color="var(--mantine-color-green-6)" />
        <Text size="lg" fw={600}>
          {t('reports:healthRecordImport.resultSummary', {
            created: importResult.total_created,
            updated: importResult.total_updated,
            skipped: importResult.total_skipped,
          })}
        </Text>
        <Table withTableBorder w="100%">
          <Table.Tbody>
            {ENTITY_ORDER.filter(
              key =>
                importResult.counts[key] &&
                (importResult.counts[key].created ||
                  importResult.counts[key].updated ||
                  importResult.counts[key].skipped)
            ).map(key => (
              <Table.Tr key={key}>
                <Table.Td>{entityLabel(key)}</Table.Td>
                <Table.Td>
                  {t('reports:healthRecordImport.resultSummary', {
                    created: importResult.counts[key].created,
                    updated: importResult.counts[key].updated,
                    skipped: importResult.counts[key].skipped,
                  })}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        {renderWarnings(importResult.warnings)}
        {renderUnmapped(importResult.unmapped)}
        <Button onClick={handleComplete}>{t('shared:labels.close')}</Button>
      </Stack>
    );
  };

  const getTitle = () => {
    switch (step) {
      case 'select':
        return t('reports:healthRecordImport.title');
      case 'preview':
        return t('reports:healthRecordImport.previewTitle');
      case 'importing':
        return t('reports:healthRecordImport.importing');
      case 'complete':
        return t('reports:healthRecordImport.resultTitle');
      default:
        return t('reports:healthRecordImport.title');
    }
  };

  return (
    <Modal
      opened={opened}
      onClose={step === 'importing' ? () => {} : onClose}
      title={getTitle()}
      size="lg"
      centered
      closeOnClickOutside={step !== 'importing'}
      closeOnEscape={step !== 'importing'}
    >
      {step === 'select' && renderSelectStep()}
      {step === 'preview' && renderPreviewStep()}
      {step === 'importing' && renderImportingStep()}
      {step === 'complete' && renderCompleteStep()}
    </Modal>
  );
};

export default HealthRecordImportModal;
