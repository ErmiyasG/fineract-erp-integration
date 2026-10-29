/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements. See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership. The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License. You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied. See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
package org.apache.fineract.accounting.glaccount.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.Optional;
import org.apache.fineract.accounting.glaccount.api.GLAccountJsonInputParams;
import org.apache.fineract.accounting.glaccount.command.GLAccountCommand;
import org.apache.fineract.accounting.glaccount.domain.GLAccount;
import org.apache.fineract.accounting.glaccount.domain.GLAccountRepository;
import org.apache.fineract.accounting.glaccount.exception.GLAccountConflictException;
import org.apache.fineract.accounting.glaccount.serialization.GLAccountCommandFromApiJsonDeserializer;
import org.apache.fineract.accounting.journalentry.domain.JournalEntryRepository;
import org.apache.fineract.infrastructure.codes.domain.CodeValueRepositoryWrapper;
import org.apache.fineract.infrastructure.core.api.JsonCommand;
import org.apache.fineract.infrastructure.core.data.CommandProcessingResult;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.jdbc.core.JdbcTemplate;

@ExtendWith(MockitoExtension.class)
class GLAccountWritePlatformServiceTest {

    @Mock
    private GLAccountRepository glAccountRepository;
    @Mock
    private JournalEntryRepository glJournalEntryRepository;
    @Mock
    private GLAccountCommandFromApiJsonDeserializer fromApiJsonDeserializer;
    @Mock
    private CodeValueRepositoryWrapper codeValueRepositoryWrapper;
    @Mock
    private JdbcTemplate jdbcTemplate;

    private GLAccountWritePlatformServiceJpaRepositoryImpl service;

    @BeforeEach
    void setUp() {
        service = new GLAccountWritePlatformServiceJpaRepositoryImpl(
                glAccountRepository,
                glJournalEntryRepository,
                fromApiJsonDeserializer,
                codeValueRepositoryWrapper,
                jdbcTemplate
        );
    }

    @Test
    void testCreateGLAccount_WithNewExternalReference_Success() {
        final String json = "{\"name\":\"Loan Receivable\",\"glCode\":\"1200\",\"type\":1,\"usage\":1,\"manualEntriesAllowed\":true,\"external_reference\":\"GL-1200\"}";
        final JsonCommand command = mock(JsonCommand.class);
        when(command.json()).thenReturn(json);
        when(command.commandId()).thenReturn(101L);
        when(command.stringValueOfParameterNamed(GLAccountJsonInputParams.EXTERNAL_REFERENCE.getValue())).thenReturn(null);
        when(command.stringValueOfParameterNamed(GLAccountJsonInputParams.EXTERNAL_REFERENCE_SNAKE.getValue())).thenReturn("GL-1200");
        when(command.stringValueOfParameterNamed(GLAccountJsonInputParams.NAME.getValue())).thenReturn("Loan Receivable");
        when(command.stringValueOfParameterNamed(GLAccountJsonInputParams.GL_CODE.getValue())).thenReturn("1200");
        when(command.longValueOfParameterNamed(GLAccountJsonInputParams.TYPE.getValue())).thenReturn(1L);
        when(command.longValueOfParameterNamed(GLAccountJsonInputParams.PARENT_ID.getValue())).thenReturn(null);
        when(command.longValueOfParameterNamed(GLAccountJsonInputParams.TAGID.getValue())).thenReturn(null);
        when(command.booleanPrimitiveValueOfParameterNamed(GLAccountJsonInputParams.DISABLED.getValue())).thenReturn(false);
        when(command.booleanPrimitiveValueOfParameterNamed(GLAccountJsonInputParams.MANUAL_ENTRIES_ALLOWED.getValue())).thenReturn(true);
        when(command.integerValueSansLocaleOfParameterNamed(GLAccountJsonInputParams.USAGE.getValue())).thenReturn(1);
        when(command.integerValueSansLocaleOfParameterNamed(GLAccountJsonInputParams.TYPE.getValue())).thenReturn(1);
        when(command.stringValueOfParameterNamed(GLAccountJsonInputParams.DESCRIPTION.getValue())).thenReturn("Asset Account");

        final GLAccountCommand accountCommand = mock(GLAccountCommand.class);
        when(fromApiJsonDeserializer.commandFromApiJson(json)).thenReturn(accountCommand);
        when(glAccountRepository.findOneByExternalReference("GL-1200")).thenReturn(Optional.empty());

        final CommandProcessingResult result = service.createGLAccount(command);

        assertNotNull(result);
        verify(accountCommand).validateForCreate();
        verify(glAccountRepository).findOneByExternalReference("GL-1200");
    }

    @Test
    void testCreateGLAccount_ExistingExternalReference_SamePayload_ReturnsExistingId() {
        final String json = "{\"name\":\"Loan Receivable\",\"glCode\":\"1200\",\"type\":1,\"external_reference\":\"GL-1200\"}";
        final JsonCommand command = mock(JsonCommand.class);
        when(command.json()).thenReturn(json);
        when(command.commandId()).thenReturn(202L);
        when(command.stringValueOfParameterNamed(GLAccountJsonInputParams.EXTERNAL_REFERENCE.getValue())).thenReturn(null);
        when(command.stringValueOfParameterNamed(GLAccountJsonInputParams.EXTERNAL_REFERENCE_SNAKE.getValue())).thenReturn("GL-1200");
        when(command.stringValueOfParameterNamed(GLAccountJsonInputParams.NAME.getValue())).thenReturn("Loan Receivable");
        when(command.stringValueOfParameterNamed(GLAccountJsonInputParams.GL_CODE.getValue())).thenReturn("1200");
        when(command.integerValueSansLocaleOfParameterNamed(GLAccountJsonInputParams.TYPE.getValue())).thenReturn(1);

        final GLAccountCommand accountCommand = mock(GLAccountCommand.class);
        when(fromApiJsonDeserializer.commandFromApiJson(json)).thenReturn(accountCommand);

        final GLAccount existing = new GLAccount();
        existing.setName("Loan Receivable");
        existing.setGlCode("1200");
        existing.setType(1);
        existing.setExternalReference("GL-1200");

        when(glAccountRepository.findOneByExternalReference("GL-1200")).thenReturn(Optional.of(existing));

        final CommandProcessingResult result = service.createGLAccount(command);

        assertNotNull(result);
        assertEquals(202L, result.getCommandId());
        verify(glAccountRepository, never()).saveAndFlush(any());
    }

    @Test
    void testCreateGLAccount_ExistingExternalReference_DifferentPayload_ThrowsConflictException() {
        final String json = "{\"name\":\"Different Name\",\"glCode\":\"1201\",\"type\":1,\"external_reference\":\"GL-1200\"}";
        final JsonCommand command = mock(JsonCommand.class);
        when(command.json()).thenReturn(json);
        when(command.stringValueOfParameterNamed(GLAccountJsonInputParams.EXTERNAL_REFERENCE.getValue())).thenReturn(null);
        when(command.stringValueOfParameterNamed(GLAccountJsonInputParams.EXTERNAL_REFERENCE_SNAKE.getValue())).thenReturn("GL-1200");
        when(command.stringValueOfParameterNamed(GLAccountJsonInputParams.NAME.getValue())).thenReturn("Different Name");
        when(command.stringValueOfParameterNamed(GLAccountJsonInputParams.GL_CODE.getValue())).thenReturn("1201");
        when(command.integerValueSansLocaleOfParameterNamed(GLAccountJsonInputParams.TYPE.getValue())).thenReturn(1);

        final GLAccountCommand accountCommand = mock(GLAccountCommand.class);
        when(fromApiJsonDeserializer.commandFromApiJson(json)).thenReturn(accountCommand);

        final GLAccount existing = new GLAccount();
        existing.setName("Loan Receivable");
        existing.setGlCode("1200");
        existing.setType(1);
        existing.setExternalReference("GL-1200");

        when(glAccountRepository.findOneByExternalReference("GL-1200")).thenReturn(Optional.of(existing));

        assertThrows(GLAccountConflictException.class, () -> service.createGLAccount(command));
    }
}
