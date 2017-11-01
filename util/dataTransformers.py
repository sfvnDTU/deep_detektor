from .baseClasses import *
import os
import re

# ==============================================================================
# Specific Concrete Data Transformers Implementations
# ==============================================================================

# Following list describes the available formats
#
#   FORMATName      : Description
#   ============================================================================
#   HTMLSource      : downloaded HTML pages from data source
#   JSONPrograms    : A JSON structure with program_id : sentences as elements
#   HTMLInput       : sentences formatted in HTML for the annotator program
#
# Tensors:
#   char            : char based representation
#   PoS             : Part-of-Speech tags
#   Xbow            : Bag-Of-Words
#   word2vec        : Word2vec
#
#

class fromHTMLSourceToJSONPrograms(BaseDataTransformer):
    """
    Transforms raw html files into JSON of the sentences and timestamps.
    """

    # Initialises class and input, output locations
    def __init__(self):
        inputFormat="HTMLSource"
        outputFormat="JSONPrograms"
        super().__init__(inputFormat,outputFormat)

    def transform(self, HTMLSource):
        files, program_id = self.getRawFilePaths(HTMLSource.data)
        JSONPrograms = self.getCleanedPrograms(files, program_id)
        return JSONPrograms

    # Auxillary Functions
    # Returns the full (or relative) path all the raw files
    def getRawFilePaths(self, HTMLSourcePath, filename='page.html', subset=None):
        program_ids = os.listdir(HTMLSourcePath)
        files = ['{:s}{:s}/{:s}'.format(HTMLSourcePath, p_id, filename) for p_id in program_ids]

        if subset and abs(subset) < len(files):
            if subset < 0:
                files = files[subset:]
                program_ids = program_ids[subset:]
            else:
                files = files[:subset]
                program_ids = program_ids[:subset]

        return files, program_ids

    def html_decode(self, s):
        """
        Returns the ASCII decoded version of the given HTML string. This does
        NOT remove normal HTML tags like <p>.
        """
        htmlCodes = (
                ("'", '&#39;'),
                ('"', '&quot;'),
                ('>', '&gt;'),
                ('<', '&lt;'),
                ('&', '&amp;')
            )
        for code in htmlCodes:
            s = s.replace(code[1], code[0])
        return s

    # Extract the relevant HTML text for each sentence
    def getHtmlSentences(self, file_loc, re_subs_pattern='category="Undertekster"[\S\W<>]+<script src="/',
                                split_pattern='<span class="digits ng-binding">'):
        subtitle_file = open(file_loc, 'r', encoding='iso-8859-1')
        #subtitle_file = open(file_loc,'r', encoding='utf-8', errors='ignore')
        doc = subtitle_file.read()
        doc = self.html_decode(doc)

        subtitle_file.close()

        # Find part of the html which contain the subtitles
        p_subs = re.compile(re_subs_pattern)
        match = re.search(p_subs,doc)
        doc_subs = match.group()

        #Split into the sentences
        sentences = doc_subs.split(split_pattern)
        sentences = [sentences[i] for i in range(1,len(sentences))]

        #print('\tProgram has {:d} sentences'.format(len(sentences)))
        return sentences


    def getTimeAndText(self, text, re_time='[\d:]+', re_text='ma-highlight="[\w?%!:-; \',.\d-]+'):
        p_time = re.compile(re_time)
        p_text = re.compile(re_text)

        text_at_this_timepoint = re.finditer(p_text,text)
        s = ''
        for match in text_at_this_timepoint:
            s=s+' '+(match.group()[14:])

        s = re.sub(' +',' ',s) # Removes repeating whitespaces
        s=s[1:] # Removes first white space

        nums = re.finditer(p_time,text)
        nums = [n.group() for n in nums]

        time_start = nums[0]
        time_end = nums[1]

        return s, time_start, time_end

    def getCleanedSentences(self, sentences):
        program = []
        start_times = []
        end_times = []

        for sen in sentences:
            text, start, end = self.getTimeAndText(sen)

            if len(program) is 0:
                program.append(text)
                start_times.append(start)
                end_times.append(end)
            elif len(text)>0:
                try:

                    if text[0] is '-':
                        last_text = program.pop()
                        s = last_text+text
                        s = re.sub('- ',' ',s)
                        s = re.sub(' -',' ',s)
                        s = re.sub(' +',' ',s)

                        program.append(s)

                        end_times.pop()
                        end_times.append(end)

                    else:
                        program.append(text)
                        start_times.append(start)
                        end_times.append(end)

                except Exception as e:
                    print('Woops.. ')
                    print('"',text,'"')
                    print(len(text))

        #print('\tProgram has {:d} cleaned sentences'.format(len(program)))
        return dict(zip(['sentences','start time','end time'],[program, start_times, end_times]))


    def getCleanedPrograms(self, file_paths, program_ids):
        total_sent = 0
        all_programs = dict()
        count = 1;
        for i in range(len(file_paths)):
            #print('Program {:d} of {:d} ({:s})'.format(i+1,len(file_paths),program_ids[i]))
            program_sentences = self.getHtmlSentences(file_paths[i])
            all_programs[program_ids[i]] = self.getCleanedSentences(program_sentences)
            total_sent += len(all_programs[program_ids[i]]['sentences'])

        #print('\nA total of {:d} sentences was found.'.format(total_sent))
        return all_programs



class fromJSONProgramsToHTMLInput(BaseDataTransformer):
    """
    Transforms JSON of the sentences and timestamps into HTML formatted
    sentences used as input files for highlighting
    """


    # Initializes class and input, output locations
    def __init__(self):
        inputFormat="JSONPrograms"
        outputFormat="HTMLInput"
        super().__init__(inputFormat,outputFormat)

    def transform(self,JSONPrograms):
        HTMLInput=self.export_debatten_programs(JSONPrograms)
        return HTMLInput

    def export_debatten_programs(self, JSONPrograms):
        FormattedSentences={}
        for p_id in JSONPrograms:
            sentenc_id = 1
            #with open(self.loc_pro_subtitles+'program'+str(p_id), 'w') as f:
            programString='<span id="program '+str(p_id)+'">\n'

            for s in JSONPrograms[p_id]['sentences']:
                programString+='\t<p id="'+str(p_id)+'"> '+s+' </p>\n'
                sentenc_id+=1

            programString+='</span>'
            FormattedSentences[str(p_id)] = programString

        return FormattedSentences
