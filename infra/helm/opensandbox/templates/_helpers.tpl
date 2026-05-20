{{/*
  _helpers.tpl — OpenSandbox Helm chart helper templates
  Plan ref: RALPLAN-DR v0.3 FINAL
*/}}

{{/*
Expand the name of the chart.
*/}}
{{- define "opensandbox.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
Truncates at 63 chars because Kubernetes DNS label limits.
*/}}
{{- define "opensandbox.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart label value (name + version).
*/}}
{{- define "opensandbox.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels — applied to every resource.
*/}}
{{- define "opensandbox.labels" -}}
helm.sh/chart: {{ include "opensandbox.chart" . }}
{{ include "opensandbox.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels — used in matchLabels / service selectors.
*/}}
{{- define "opensandbox.selectorLabels" -}}
app.kubernetes.io/name: {{ include "opensandbox.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Controller-specific selector labels.
*/}}
{{- define "opensandbox.controller.selectorLabels" -}}
app.kubernetes.io/name: {{ include "opensandbox.name" . }}-controller
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: controller
{{- end }}

{{/*
execd-specific selector labels.
*/}}
{{- define "opensandbox.execd.selectorLabels" -}}
app.kubernetes.io/name: {{ include "opensandbox.name" . }}-execd
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: execd
{{- end }}

{{/*
image-prewarm-specific selector labels.
*/}}
{{- define "opensandbox.prewarm.selectorLabels" -}}
app.kubernetes.io/name: {{ include "opensandbox.name" . }}-prewarm
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: image-prewarm
{{- end }}

{{/*
Controller ServiceAccount name.
*/}}
{{- define "opensandbox.controller.serviceAccountName" -}}
opensandbox-controller
{{- end }}

{{/*
execd ServiceAccount name.
*/}}
{{- define "opensandbox.execd.serviceAccountName" -}}
opensandbox-execd
{{- end }}

{{/*
Image pre-warm ServiceAccount name.
*/}}
{{- define "opensandbox.prewarm.serviceAccountName" -}}
opensandbox-prewarm
{{- end }}

{{/*
System namespace where all OpenSandbox control-plane components live.
*/}}
{{- define "opensandbox.systemNamespace" -}}
opensandbox-system
{{- end }}

{{/*
Render a container image reference from repository + tag.
Usage: {{ include "opensandbox.image" (dict "repository" .Values.controller.image.repository "tag" .Values.controller.image.tag) }}
*/}}
{{- define "opensandbox.image" -}}
{{- if .tag }}
{{- printf "%s:%s" .repository .tag }}
{{- else }}
{{- .repository }}
{{- end }}
{{- end }}
