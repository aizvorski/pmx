import glob
import luigi
import os
import subprocess
from luigi.parameter import ParameterVisibility
from pmx.scripts.workflows.SGE_tasks.SGETunedArrayJobTask import SGETunedArrayJobTask #tuned for the owl cluster
from pmx.scripts.workflows.SGE_tasks.absFE.LinP.morphes import Task_PL_gen_morphes
from pmx.scripts.workflows.SGE_tasks.absFE.LinP.restraints import Task_PL_gen_restraints
from pmx.scripts.workflows.SGE_tasks.absFE.LinP.restraints_align2crystal import Task_PL_gen_restraints_align2crystal
from pmx.scripts.workflows.SGE_tasks.absFE.LinP.morphes_align2crystal import Task_PL_gen_morphes_align2crystal
from pmx.scripts.workflows.utils import read_from_mdp


class Task_PL_TI_simArray(SGETunedArrayJobTask):

    #Parameters:
    p = luigi.Parameter(description='Protein name')
    l = luigi.Parameter(description='Ligand name')
    i = luigi.IntParameter(description='Repeat number')
    m = luigi.IntParameter(description='Sampling sim number')
    sTI = luigi.Parameter(description='Coupling state')

    folder_path = luigi.Parameter(significant=False,
                 visibility=ParameterVisibility.HIDDEN,
                 description='Path to the protein+ligand folder to set up')

    study_settings = luigi.DictParameter(significant=False,
                 visibility=ParameterVisibility.HIDDEN,
                 description='Dict of study stettings '
                      'used to propagate settings to dependencies')

    restr_scheme = luigi.Parameter(significant=True,
                 description='Restraint scheme to use. '
                 'Aligned, Aligned_crystal, Fitted or Fixed')
                 
    target_success_ratio = luigi.FloatParameter(significant=False,
                 visibility=ParameterVisibility.HIDDEN,
                 default=0.90,
                 description='Successful TI runs ratio before proceding.')

    stage="morphes"
    #request 1 core
    n_cpu = luigi.IntParameter(visibility=ParameterVisibility.HIDDEN,
                               default=1, significant=False)

    job_name_format = luigi.Parameter(
        visibility=ParameterVisibility.HIDDEN,
        significant=False, default="pmx_{task_family}_p{p}_l{l}_{sTI}{i}_{m}",
        description="A string that can be "
        "formatted with class variables to name the job with qsub.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        #set variables
        self.base_path = self.study_settings['base_path']
        if(self.restr_scheme=="Aligned" or not self.restr_scheme): #not set in WinL
            self.sim_path = self.folder_path+"/state%s/repeat%d/%s%d"%(
                self.sTI, self.i, self.stage, self.m)
            self.top = self.folder_path+"/topolTI_ions{2}{3}_{4}.top".format(
                self.p, self.l, self.sTI, self.i, self.m)
        elif(self.restr_scheme=="Aligned_crystal"):
            self.sim_path = self.folder_path+"/state%s/repeat%d/aligned2crystal_%s%d"%(
            self.sTI, self.i, self.stage, self.m)
            self.top = self.folder_path+"/topolTI_aligned2crystal_ions{3}_{4}.top".format(
                self.p, self.l, self.sTI, self.i, self.m)
        else:
            raise(Exception("Unsupported restr_scheme: '%s'"%self.restr_scheme))

        self.mdp = self.study_settings['mdp_path'] +\
            "/protein/ti_{0}.mdp".format(
                self.study_settings['TIstates'][self.sTI])

        self.mdrun = self.study_settings['mdrun']
        self.mdrun_opts = self.study_settings['mdrun_opts']

    def requires(self):
        tasks=[]
        if(self.restr_scheme=="Aligned"):
            if(self.sTI=='A'):
                tasks.append( Task_PL_gen_morphes(p=self.p, l=self.l,
                              i=self.i, m=self.m, sTI=self.sTI,
                              study_settings=self.study_settings,
                              folder_path=self.folder_path,
                              parallel_env=self.parallel_env,
                              restr_scheme=self.restr_scheme) )
            elif(self.sTI=='C'):
                tasks.append( Task_PL_gen_restraints(p=self.p, l=self.l,
                              i=self.i,
                              study_settings=self.study_settings,
                              folder_path=self.folder_path,
                              parallel_env=self.parallel_env,
                              restr_scheme=self.restr_scheme) )
            else:
                raise(Exception("Unsupported TI state detected."))
        elif(self.restr_scheme=="Aligned_crystal"):
            #need restr anyway
            tasks.append( Task_PL_gen_restraints_align2crystal(
                          p=self.p, l=self.l,
                          study_settings=self.study_settings,
                          folder_path=self.folder_path,
                          parallel_env=self.parallel_env,
                          restr_scheme=self.restr_scheme) )

            #A also needs morphes
            if(self.sTI=='A'):
                tasks.append( Task_PL_gen_morphes_align2crystal(p=self.p, l=self.l,
                              i=self.i, m=self.m, sTI=self.sTI,
                              study_settings=self.study_settings,
                              folder_path=self.folder_path,
                              parallel_env=self.parallel_env,
                              restr_scheme=self.restr_scheme) )
            elif(self.sTI!='D'):
                raise(Exception("Unsupported TI state detected."))
        else:
            raise(Exception("Unsupported restraint scheme '%s'"%self.restr_scheme))

        return(tasks)



    def output(self):
        #nframes = len(glob.glob1(self.sim_path,"frame*.gro"))
        nframes = len(glob.glob(self.sim_path+"/frame*.gro", recursive=True))
        if(nframes==0):
            raise(Exception("No frames to run TI on in "+self.sim_path))

        targets=[]
        for nf in range(nframes):
            targets.append(luigi.LocalTarget(
                os.path.join(self.sim_path, 'dHdl%d.xvg'%nf)) )
        return targets

    def complete(self):
        """
        Check if all dHdl files exist and are of correct length.
        """
        reqs_complete = all(r.complete() for r in luigi.task.flatten(self.requires()))
        if(reqs_complete):
            #print("\t\treqs_complete\n")
            outputs = luigi.task.flatten(self.output())
            exist = list(map(lambda output: output.exists(), outputs))
            unfinished = self._find_unfinished()
            success_ratio = 1.0 - (float(len(unfinished))/float(len(exist)))
            return (success_ratio>=self.target_success_ratio)
        else:
            return(reqs_complete)


    def _find_unfinished(self):
        """Finds the list of unfinished jobs in the job array
        which need to be (re)run.

        Overloads implementation in SGETunedArrayJobTask.
        Needs to be executed after self.__init__(), where self.mdp is set,
        but before pickling in SGETunedJobTask._init_local() .

        Will be called in self._init_local() before pickling
        an instance of this class for execution on the worker nodes
        so the nodes can map SGE_TASK_ID to the correct jobs.

        Parameters
        ----------
        None.

        Returns
        -------
        List of unfinished dHdl*.xvg ids in the job array.
        """
        expected_end_time, dtframe = read_from_mdp(self.mdp)
        nframes = len(glob.glob1(self.sim_path,"frame*.gro"))

        unf=[]
        for nf in range(nframes):
            fname=os.path.join(self.sim_path, 'dHdl%d.xvg'%nf)
            if(os.path.isfile(fname)):
                try:
                    last_line = subprocess.check_output(
                        ['tail', '-1', fname]).decode('utf-8')
                    last_time = float(last_line.split()[0])
                    if(last_time == expected_end_time):
                        continue;
                except IndexError: #if some dDdl file exists but is empty
                    pass;

            #frame not ready
            unf.append(nf)
        return(unf)

    def work(self):
        os.makedirs(self.sim_path, exist_ok=True)
        os.chdir(self.sim_path)

        #find which task this is
        SGE_TASK_ID = int(os.environ['SGE_TASK_ID'])

        #translate it into a non-finished frame ID
        print("unfinished:",self.unfinished)
        dHdL_id=self.unfinished[SGE_TASK_ID-1] #SGE starts counting from 1

        #find temp working dir for this job
        TMPDIR = os.environ['TMPDIR']

        #make tpr
        os.system("gmx grompp -p {top} -c frame{_id}.gro "
                  "-o {D}/ti.tpr -po {D}/mdout.mdp -f {mdp} "
                  "-v -maxwarn 3 ".format(D=TMPDIR, top=self.top,
                                          mdp=self.mdp, _id=dHdL_id) )

        #limit mdrun runtime
        s = self.runtime.split(':')
        maxh = (int(s[0])+float(s[1])/60+float(s[2])/3600)
        maxh = max(maxh*0.95, maxh-0.02) #grace period of 72 s so that SGE doesn't kill it too fast

        #run sim
        os.system(self.mdrun+" -s {D}/ti.tpr -dhdl {D}/dgdl.xvg -cpo "
                  "{D}/state.cpt -e {D}/ener.edr -g {D}/md.log -o "
                  "{D}/traj.trr -x {D}/traj.xtc -c {D}/confout.gro "
                  "-ntomp {n_cpu} -maxh {maxh} {opts}".format(
                      D=TMPDIR, n_cpu=self.n_cpu, maxh=maxh,
                      opts=self.mdrun_opts) )

        #copy dHdl file back
        os.system("rsync {}/dgdl.xvg {}/dHdl{}.xvg".format(
                      TMPDIR, self.sim_path, dHdL_id) )

        #Return to basepath
        os.chdir(self.base_path)
